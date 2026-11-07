//! Registro de atestaciones de reportes de Lumen Lupa, solo por hash (D-16, contrato T7).
//!
//! Cada reporte de integridad de mercado es privado (D-03). Este contrato guarda solo su huella: el
//! sha256 del reporte, de la metodología y de los datos, la versión de la metodología y la ventana
//! analizada. Quien recibe un reporte puede comprobar en la cadena que es auténtico, que no fue
//! alterado, cuándo se publicó y con qué metodología.
//!
//! Por construcción no puede guardar nombres de tokens, cuentas ni cifras de volumen: los parámetros
//! de la interfaz pública solo son `BytesN<32>`, `u32`, `u64` y `Address`, y una atestación solo
//! agrega un `bool`. Ningún parámetro ni campo es `String`, `Symbol` ni `Bytes` de longitud libre.
//! Las pruebas lo comprueban sobre el spec que generan las macros (`src/test.rs`) y, en CI, sobre el
//! spec del WASM compilado (`tests/wasm.rs`).
//!
//! API usada: soroban-sdk 29.0.1 (constructor `__constructor`, `#[contracterror]`,
//! `#[contractevent]`, almacenamiento `instance` y `persistent` con `extend_ttl`).
#![no_std]

use soroban_sdk::{
    contract, contracterror, contractevent, contractimpl, contracttype, symbol_short, Address,
    BytesN, Env, Symbol,
};

#[cfg(test)]
mod test;

/// Versión del código de este contrato (la devuelve `version()`). Sube si cambian la interfaz o las reglas.
pub const VERSION: u32 = 1;

/// Ledgers en un día, con la meta actual de cierre de ~5 s por ledger. Es una aproximación: el tiempo
/// de cierre es un parámetro de la red. Fuente: guía oficial "Storage strategies"
/// (https://developers.stellar.org/docs/build/guides/storage/storage-strategies), consultada el
/// 6-nov-2026.
pub const DIA_EN_LEDGERS: u32 = 17_280;

/// Si al publicar o consultar una atestación le quedan `UMBRAL_TTL_ATESTACION` ledgers o menos de
/// vida (≈119 días), se extiende a `EXTENSION_TTL_ATESTACION`.
pub const UMBRAL_TTL_ATESTACION: u32 = 119 * DIA_EN_LEDGERS;

/// TTL al que se lleva una atestación al extenderla (≈120 días). Queda por debajo del TTL máximo que
/// cita la misma guía (3,110,400 ledgers, ≈180 días); si la red lo bajara, el host recorta las
/// entradas persistentes al máximo en lugar de fallar.
pub const EXTENSION_TTL_ATESTACION: u32 = 120 * DIA_EN_LEDGERS;

/// Igual que `UMBRAL_TTL_ATESTACION`, para la instancia del contrato (donde vive el admin) y su código.
pub const UMBRAL_TTL_INSTANCIA: u32 = 119 * DIA_EN_LEDGERS;

/// Igual que `EXTENSION_TTL_ATESTACION`, para la instancia del contrato y su código.
pub const EXTENSION_TTL_INSTANCIA: u32 = 120 * DIA_EN_LEDGERS;

// Comprobado al compilar: el host rechaza `extend_ttl` si el umbral es mayor que la extensión.
const _: () = assert!(UMBRAL_TTL_ATESTACION < EXTENSION_TTL_ATESTACION);
const _: () = assert!(UMBRAL_TTL_INSTANCIA < EXTENSION_TTL_INSTANCIA);

/// Clave del admin en el almacenamiento de instancia.
const CLAVE_ADMIN: Symbol = symbol_short!("admin");

/// Prefijo de la clave de cada atestación en el almacenamiento persistente: `(PREFIJO, hash_reporte)`.
/// Las claves son una tupla y un símbolo (no un `#[contracttype]` interno) para que el spec del contrato
/// solo tenga tipos que cruzan la interfaz.
const PREFIJO_ATESTACION: Symbol = symbol_short!("atest");

/// Datos de una atestación. La clave es el `hash_reporte` (sha256 de los bytes de `reporte.json`).
#[contracttype]
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct Atestacion {
    /// sha256 de `methodology_version` más el JSON canónico de los parámetros del reporte.
    pub hash_metodologia: BytesN<32>,
    /// sha256 de las filas de entrada del reporte, en forma canónica.
    pub hash_datos: BytesN<32>,
    /// Versión numérica de la metodología.
    pub version_metodologia: u32,
    /// Inicio de la ventana analizada, en segundos Unix.
    pub ventana_inicio: u64,
    /// Fin de la ventana analizada, en segundos Unix. Siempre mayor que `ventana_inicio`.
    pub ventana_fin: u64,
    /// Timestamp del ledger en que se publicó (segundos Unix).
    pub publicada_en: u64,
    /// Admin que publicó la atestación.
    pub publicador: Address,
    /// `true` si el admin la revocó. Revocar no borra la atestación.
    pub revocada: bool,
}

/// Errores del contrato. Los códigos son estables: no se reutilizan ni se renumeran.
#[contracterror]
#[derive(Copy, Clone, Debug, Eq, PartialEq, PartialOrd, Ord)]
#[repr(u32)]
pub enum Error {
    /// Ya hay una atestación con ese `hash_reporte`; no se sobrescribe.
    YaExiste = 1,
    /// `ventana_inicio` es mayor o igual que `ventana_fin`.
    VentanaInvalida = 2,
    /// No hay atestación con ese `hash_reporte`.
    NoExiste = 3,
    /// La atestación ya estaba revocada.
    YaRevocada = 4,
    /// El admin nuevo es el mismo que el actual.
    MismoAdmin = 5,
}

/// Evento de `publicar`. Tema: `["atestacion_publicada", hash_reporte]`; datos: el resto de la atestación.
#[contractevent]
pub struct AtestacionPublicada {
    #[topic]
    pub hash_reporte: BytesN<32>,
    pub hash_metodologia: BytesN<32>,
    pub hash_datos: BytesN<32>,
    pub version_metodologia: u32,
    pub ventana_inicio: u64,
    pub ventana_fin: u64,
    pub publicada_en: u64,
    pub publicador: Address,
}

/// Evento de `revocar`. Tema: `["atestacion_revocada", hash_reporte]`; datos: el admin que revocó.
#[contractevent]
pub struct AtestacionRevocada {
    #[topic]
    pub hash_reporte: BytesN<32>,
    pub admin: Address,
}

/// Evento de `cambiar_admin`. Tema: `["admin_cambiado"]`; datos: el admin anterior y el nuevo.
#[contractevent]
pub struct AdminCambiado {
    pub anterior: Address,
    pub nuevo: Address,
}

#[contract]
pub struct Atestaciones;

#[contractimpl]
impl Atestaciones {
    /// Fija el admin una sola vez, al desplegar el contrato (constructor de Soroban).
    pub fn __constructor(env: Env, admin: Address) {
        env.storage().instance().set(&CLAVE_ADMIN, &admin);
        extender_instancia(&env);
    }

    /// Registra la atestación de un reporte. Solo el admin. Falla con `YaExiste` si el hash ya está
    /// registrado (nunca sobrescribe) y con `VentanaInvalida` si `ventana_inicio >= ventana_fin`.
    pub fn publicar(
        env: Env,
        hash_reporte: BytesN<32>,
        hash_metodologia: BytesN<32>,
        hash_datos: BytesN<32>,
        version_metodologia: u32,
        ventana_inicio: u64,
        ventana_fin: u64,
    ) -> Result<(), Error> {
        let admin = leer_admin(&env);
        admin.require_auth();
        if ventana_inicio >= ventana_fin {
            return Err(Error::VentanaInvalida);
        }
        let clave = clave_atestacion(&hash_reporte);
        let almacen = env.storage().persistent();
        if almacen.has(&clave) {
            return Err(Error::YaExiste);
        }
        let publicada_en = env.ledger().timestamp();
        let atestacion = Atestacion {
            hash_metodologia: hash_metodologia.clone(),
            hash_datos: hash_datos.clone(),
            version_metodologia,
            ventana_inicio,
            ventana_fin,
            publicada_en,
            publicador: admin.clone(),
            revocada: false,
        };
        almacen.set(&clave, &atestacion);
        almacen.extend_ttl(&clave, UMBRAL_TTL_ATESTACION, EXTENSION_TTL_ATESTACION);
        extender_instancia(&env);
        AtestacionPublicada {
            hash_reporte,
            hash_metodologia,
            hash_datos,
            version_metodologia,
            ventana_inicio,
            ventana_fin,
            publicada_en,
            publicador: admin,
        }
        .publish(&env);
        Ok(())
    }

    /// Devuelve la atestación de un reporte, o `None` si no existe. Cualquiera puede consultar. Si
    /// existe, extiende su TTL (y el de la instancia) cuando baja del umbral.
    pub fn consultar(env: Env, hash_reporte: BytesN<32>) -> Option<Atestacion> {
        let clave = clave_atestacion(&hash_reporte);
        let almacen = env.storage().persistent();
        let atestacion: Option<Atestacion> = almacen.get(&clave);
        if atestacion.is_some() {
            almacen.extend_ttl(&clave, UMBRAL_TTL_ATESTACION, EXTENSION_TTL_ATESTACION);
        }
        extender_instancia(&env);
        atestacion
    }

    /// Marca una atestación como revocada; no la borra. Solo el admin. Falla con `NoExiste` si el hash
    /// no está registrado y con `YaRevocada` si ya estaba revocada.
    pub fn revocar(env: Env, hash_reporte: BytesN<32>) -> Result<(), Error> {
        let admin = leer_admin(&env);
        admin.require_auth();
        let clave = clave_atestacion(&hash_reporte);
        let almacen = env.storage().persistent();
        let mut atestacion: Atestacion = almacen.get(&clave).ok_or(Error::NoExiste)?;
        if atestacion.revocada {
            return Err(Error::YaRevocada);
        }
        atestacion.revocada = true;
        almacen.set(&clave, &atestacion);
        almacen.extend_ttl(&clave, UMBRAL_TTL_ATESTACION, EXTENSION_TTL_ATESTACION);
        extender_instancia(&env);
        AtestacionRevocada {
            hash_reporte,
            admin,
        }
        .publish(&env);
        Ok(())
    }

    /// Cambia el admin. Exige la autorización del admin actual y la del nuevo (el nuevo demuestra que
    /// controla la dirección). Falla con `MismoAdmin` si el nuevo es el actual.
    pub fn cambiar_admin(env: Env, nuevo: Address) -> Result<(), Error> {
        let actual = leer_admin(&env);
        if nuevo == actual {
            return Err(Error::MismoAdmin);
        }
        actual.require_auth();
        nuevo.require_auth();
        env.storage().instance().set(&CLAVE_ADMIN, &nuevo);
        extender_instancia(&env);
        AdminCambiado {
            anterior: actual,
            nuevo,
        }
        .publish(&env);
        Ok(())
    }

    /// Devuelve el admin actual.
    pub fn admin(env: Env) -> Address {
        leer_admin(&env)
    }

    /// Devuelve la versión del código del contrato (`VERSION`).
    pub fn version() -> u32 {
        VERSION
    }
}

/// Lee el admin. Siempre existe: el constructor lo fija al desplegar y nada lo borra.
fn leer_admin(env: &Env) -> Address {
    env.storage().instance().get(&CLAVE_ADMIN).unwrap()
}

/// Clave persistente de una atestación.
fn clave_atestacion(hash_reporte: &BytesN<32>) -> (Symbol, BytesN<32>) {
    (PREFIJO_ATESTACION, hash_reporte.clone())
}

/// Extiende el TTL de la instancia del contrato y de su código cuando baja del umbral.
fn extender_instancia(env: &Env) {
    env.storage()
        .instance()
        .extend_ttl(UMBRAL_TTL_INSTANCIA, EXTENSION_TTL_INSTANCIA);
}
