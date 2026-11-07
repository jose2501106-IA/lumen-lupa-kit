//! Pruebas del contrato de atestaciones. Corren en el host con la feature `testutils` de soroban-sdk:
//! sin red, sin llaves y sin desplegar nada.
#![cfg(test)]
extern crate std;

use std::{string::String, vec::Vec as StdVec};

use soroban_sdk::{
    testutils::{
        storage::{Instance as _, Persistent as _},
        Address as _, AuthorizedFunction, AuthorizedInvocation, Events as _, Ledger as _, MockAuth,
        MockAuthInvoke,
    },
    xdr::{self, ReadXdr},
    Address, BytesN, Env, Event as _, IntoVal, InvokeError,
};

use super::*;

/// Timestamp fijo del ledger en las pruebas (segundos Unix; 2026-11-06 00:00:00 UTC).
const AHORA: u64 = 1_793_923_200;
/// Ventana de ejemplo: 2026-09-21 00:00:00 a 2026-10-04 23:59:59 UTC (la del estudio, D-15).
const INICIO: u64 = 1_789_948_800;
const FIN: u64 = 1_791_158_399;

struct Contexto {
    env: Env,
    id: Address,
    admin: Address,
}

impl Contexto {
    fn nuevo() -> Self {
        let env = Env::default();
        env.ledger().set_timestamp(AHORA);
        env.ledger().set_sequence_number(1_000);
        let admin = Address::generate(&env);
        let id = env.register(Atestaciones, (&admin,));
        Contexto { env, id, admin }
    }

    fn cliente(&self) -> AtestacionesClient<'_> {
        AtestacionesClient::new(&self.env, &self.id)
    }

    fn hash(&self, byte: u8) -> BytesN<32> {
        BytesN::from_array(&self.env, &[byte; 32])
    }

    /// Autoriza solo a `quien` para llamar `funcion` con `args`; cualquier otra firma requerida falla.
    fn autorizar(&self, quien: &Address, funcion: &str, args: soroban_sdk::Vec<soroban_sdk::Val>) {
        self.env.mock_auths(&[MockAuth {
            address: quien,
            invoke: &MockAuthInvoke {
                contract: &self.id,
                fn_name: funcion,
                args,
                sub_invokes: &[],
            },
        }]);
    }

    fn ttl_atestacion(&self, hash: &BytesN<32>) -> u32 {
        self.env.as_contract(&self.id, || {
            self.env
                .storage()
                .persistent()
                .get_ttl(&clave_atestacion(hash))
        })
    }

    fn ttl_instancia(&self) -> u32 {
        self.env
            .as_contract(&self.id, || self.env.storage().instance().get_ttl())
    }
}

fn atestacion_esperada(ctx: &Contexto) -> Atestacion {
    Atestacion {
        hash_metodologia: ctx.hash(2),
        hash_datos: ctx.hash(3),
        version_metodologia: 1,
        ventana_inicio: INICIO,
        ventana_fin: FIN,
        publicada_en: AHORA,
        publicador: ctx.admin.clone(),
        revocada: false,
    }
}

fn publicar_ejemplo(ctx: &Contexto) {
    ctx.cliente()
        .publicar(&ctx.hash(1), &ctx.hash(2), &ctx.hash(3), &1, &INICIO, &FIN);
}

// ---------------------------------------------------------------------------------------------
// publicar y consultar

#[test]
fn publicar_y_consultar() {
    let ctx = Contexto::nuevo();
    ctx.env.mock_all_auths();
    publicar_ejemplo(&ctx);

    // La firma que pidió el contrato es la del admin, para publicar con esos argumentos exactos.
    assert_eq!(
        ctx.env.auths(),
        std::vec![(
            ctx.admin.clone(),
            AuthorizedInvocation {
                function: AuthorizedFunction::Contract((
                    ctx.id.clone(),
                    soroban_sdk::Symbol::new(&ctx.env, "publicar"),
                    (ctx.hash(1), ctx.hash(2), ctx.hash(3), 1u32, INICIO, FIN).into_val(&ctx.env),
                )),
                sub_invocations: std::vec![],
            }
        )]
    );
    assert_eq!(
        ctx.cliente().consultar(&ctx.hash(1)),
        Some(atestacion_esperada(&ctx))
    );
}

#[test]
fn consultar_hash_inexistente_devuelve_none() {
    let ctx = Contexto::nuevo();
    assert_eq!(ctx.cliente().consultar(&ctx.hash(9)), None);
    // Y sigue en None aunque exista otra atestación.
    ctx.env.mock_all_auths();
    publicar_ejemplo(&ctx);
    assert_eq!(ctx.cliente().consultar(&ctx.hash(9)), None);
}

#[test]
fn publicar_duplicado_rechazado_sin_sobrescribir() {
    let ctx = Contexto::nuevo();
    ctx.env.mock_all_auths();
    publicar_ejemplo(&ctx);

    // Mismo hash de reporte con otros datos: falla y la original queda igual.
    let resultado =
        ctx.cliente()
            .try_publicar(&ctx.hash(1), &ctx.hash(7), &ctx.hash(8), &2, &1, &2);
    assert_eq!(resultado, Err(Ok(Error::YaExiste)));
    assert_eq!(
        ctx.cliente().consultar(&ctx.hash(1)),
        Some(atestacion_esperada(&ctx))
    );

    // También si la atestación ya está revocada: un hash registrado no se vuelve a publicar.
    ctx.cliente().revocar(&ctx.hash(1));
    let resultado =
        ctx.cliente()
            .try_publicar(&ctx.hash(1), &ctx.hash(2), &ctx.hash(3), &1, &INICIO, &FIN);
    assert_eq!(resultado, Err(Ok(Error::YaExiste)));
}

#[test]
fn publicar_sin_autorizacion_del_admin_rechazado() {
    let ctx = Contexto::nuevo();

    // Sin ninguna firma.
    let resultado =
        ctx.cliente()
            .try_publicar(&ctx.hash(1), &ctx.hash(2), &ctx.hash(3), &1, &INICIO, &FIN);
    assert_eq!(resultado, Err(Err(InvokeError::Abort)));

    // Con la firma de otra cuenta que no es el admin.
    let intruso = Address::generate(&ctx.env);
    ctx.autorizar(
        &intruso,
        "publicar",
        (ctx.hash(1), ctx.hash(2), ctx.hash(3), 1u32, INICIO, FIN).into_val(&ctx.env),
    );
    let resultado =
        ctx.cliente()
            .try_publicar(&ctx.hash(1), &ctx.hash(2), &ctx.hash(3), &1, &INICIO, &FIN);
    assert_eq!(resultado, Err(Err(InvokeError::Abort)));

    // La firma del admin para OTROS argumentos tampoco sirve.
    ctx.autorizar(
        &ctx.admin,
        "publicar",
        (ctx.hash(1), ctx.hash(2), ctx.hash(4), 1u32, INICIO, FIN).into_val(&ctx.env),
    );
    let resultado =
        ctx.cliente()
            .try_publicar(&ctx.hash(1), &ctx.hash(2), &ctx.hash(3), &1, &INICIO, &FIN);
    assert_eq!(resultado, Err(Err(InvokeError::Abort)));

    assert_eq!(ctx.cliente().consultar(&ctx.hash(1)), None);

    // Con la firma exacta del admin, sí.
    ctx.autorizar(
        &ctx.admin,
        "publicar",
        (ctx.hash(1), ctx.hash(2), ctx.hash(3), 1u32, INICIO, FIN).into_val(&ctx.env),
    );
    ctx.cliente()
        .publicar(&ctx.hash(1), &ctx.hash(2), &ctx.hash(3), &1, &INICIO, &FIN);
    assert_eq!(
        ctx.cliente().consultar(&ctx.hash(1)),
        Some(atestacion_esperada(&ctx))
    );
}

#[test]
fn ventana_invalida_rechazada() {
    let ctx = Contexto::nuevo();
    ctx.env.mock_all_auths();
    // Inicio igual al fin.
    let resultado =
        ctx.cliente()
            .try_publicar(&ctx.hash(1), &ctx.hash(2), &ctx.hash(3), &1, &FIN, &FIN);
    assert_eq!(resultado, Err(Ok(Error::VentanaInvalida)));
    // Inicio después del fin.
    let resultado =
        ctx.cliente()
            .try_publicar(&ctx.hash(1), &ctx.hash(2), &ctx.hash(3), &1, &FIN, &INICIO);
    assert_eq!(resultado, Err(Ok(Error::VentanaInvalida)));
    assert_eq!(ctx.cliente().consultar(&ctx.hash(1)), None);
    // Un segundo de ventana es válido.
    ctx.cliente().publicar(
        &ctx.hash(1),
        &ctx.hash(2),
        &ctx.hash(3),
        &1,
        &INICIO,
        &(INICIO + 1),
    );
}

// ---------------------------------------------------------------------------------------------
// revocar

#[test]
fn revocar_marca_sin_borrar() {
    let ctx = Contexto::nuevo();
    ctx.env.mock_all_auths();
    publicar_ejemplo(&ctx);

    ctx.cliente().revocar(&ctx.hash(1));
    assert_eq!(
        ctx.env.auths(),
        std::vec![(
            ctx.admin.clone(),
            AuthorizedInvocation {
                function: AuthorizedFunction::Contract((
                    ctx.id.clone(),
                    soroban_sdk::Symbol::new(&ctx.env, "revocar"),
                    (ctx.hash(1),).into_val(&ctx.env),
                )),
                sub_invocations: std::vec![],
            }
        )]
    );
    let mut esperada = atestacion_esperada(&ctx);
    esperada.revocada = true;
    assert_eq!(ctx.cliente().consultar(&ctx.hash(1)), Some(esperada));

    // Revocar dos veces o revocar un hash que no existe: error con código estable.
    assert_eq!(
        ctx.cliente().try_revocar(&ctx.hash(1)),
        Err(Ok(Error::YaRevocada))
    );
    assert_eq!(
        ctx.cliente().try_revocar(&ctx.hash(9)),
        Err(Ok(Error::NoExiste))
    );
}

#[test]
fn revocar_sin_autorizacion_del_admin_rechazado() {
    let ctx = Contexto::nuevo();
    ctx.env.mock_all_auths();
    publicar_ejemplo(&ctx);

    let intruso = Address::generate(&ctx.env);
    ctx.autorizar(&intruso, "revocar", (ctx.hash(1),).into_val(&ctx.env));
    assert_eq!(
        ctx.cliente().try_revocar(&ctx.hash(1)),
        Err(Err(InvokeError::Abort))
    );
    assert_eq!(
        ctx.cliente().consultar(&ctx.hash(1)),
        Some(atestacion_esperada(&ctx))
    );
}

// ---------------------------------------------------------------------------------------------
// cambiar_admin

#[test]
fn cambiar_admin_con_doble_autorizacion() {
    let ctx = Contexto::nuevo();
    let nuevo = Address::generate(&ctx.env);
    ctx.env.mock_all_auths();
    ctx.cliente().cambiar_admin(&nuevo);

    // Firmaron los dos: el admin actual y el nuevo.
    let invocacion = AuthorizedInvocation {
        function: AuthorizedFunction::Contract((
            ctx.id.clone(),
            soroban_sdk::Symbol::new(&ctx.env, "cambiar_admin"),
            (nuevo.clone(),).into_val(&ctx.env),
        )),
        sub_invocations: std::vec![],
    };
    assert_eq!(
        ctx.env.auths(),
        std::vec![
            (ctx.admin.clone(), invocacion.clone()),
            (nuevo.clone(), invocacion)
        ]
    );
    assert_eq!(ctx.cliente().admin(), nuevo);
}

#[test]
fn cambiar_admin_falla_si_falta_una_de_las_dos_firmas() {
    let ctx = Contexto::nuevo();
    let nuevo = Address::generate(&ctx.env);
    let args: soroban_sdk::Vec<soroban_sdk::Val> = (nuevo.clone(),).into_val(&ctx.env);

    // Solo firma el admin actual.
    ctx.autorizar(&ctx.admin, "cambiar_admin", args.clone());
    assert_eq!(
        ctx.cliente().try_cambiar_admin(&nuevo),
        Err(Err(InvokeError::Abort))
    );
    // Solo firma el nuevo.
    ctx.autorizar(&nuevo, "cambiar_admin", args);
    assert_eq!(
        ctx.cliente().try_cambiar_admin(&nuevo),
        Err(Err(InvokeError::Abort))
    );

    assert_eq!(ctx.cliente().admin(), ctx.admin);
}

#[test]
fn cambiar_admin_al_mismo_rechazado() {
    let ctx = Contexto::nuevo();
    ctx.env.mock_all_auths();
    assert_eq!(
        ctx.cliente().try_cambiar_admin(&ctx.admin),
        Err(Ok(Error::MismoAdmin))
    );
    assert_eq!(ctx.cliente().admin(), ctx.admin);
}

#[test]
fn tras_cambiar_admin_solo_el_nuevo_publica() {
    let ctx = Contexto::nuevo();
    let nuevo = Address::generate(&ctx.env);
    ctx.env.mock_all_auths();
    ctx.cliente().cambiar_admin(&nuevo);

    let args: soroban_sdk::Vec<soroban_sdk::Val> =
        (ctx.hash(1), ctx.hash(2), ctx.hash(3), 1u32, INICIO, FIN).into_val(&ctx.env);
    ctx.autorizar(&ctx.admin, "publicar", args.clone());
    let resultado =
        ctx.cliente()
            .try_publicar(&ctx.hash(1), &ctx.hash(2), &ctx.hash(3), &1, &INICIO, &FIN);
    assert_eq!(resultado, Err(Err(InvokeError::Abort)));

    ctx.autorizar(&nuevo, "publicar", args);
    ctx.cliente()
        .publicar(&ctx.hash(1), &ctx.hash(2), &ctx.hash(3), &1, &INICIO, &FIN);
    let atestacion = ctx.cliente().consultar(&ctx.hash(1)).unwrap();
    assert_eq!(atestacion.publicador, nuevo);
}

// ---------------------------------------------------------------------------------------------
// eventos

#[test]
fn evento_emitido_al_publicar() {
    let ctx = Contexto::nuevo();
    ctx.env.mock_all_auths();
    publicar_ejemplo(&ctx);
    let esperado = AtestacionPublicada {
        hash_reporte: ctx.hash(1),
        hash_metodologia: ctx.hash(2),
        hash_datos: ctx.hash(3),
        version_metodologia: 1,
        ventana_inicio: INICIO,
        ventana_fin: FIN,
        publicada_en: AHORA,
        publicador: ctx.admin.clone(),
    };
    assert_eq!(
        ctx.env.events().all().filter_by_contract(&ctx.id),
        std::vec![esperado.to_xdr(&ctx.env, &ctx.id)]
    );
}

#[test]
fn eventos_al_revocar_y_al_cambiar_admin() {
    let ctx = Contexto::nuevo();
    ctx.env.mock_all_auths();
    publicar_ejemplo(&ctx);

    ctx.cliente().revocar(&ctx.hash(1));
    let revocada = AtestacionRevocada {
        hash_reporte: ctx.hash(1),
        admin: ctx.admin.clone(),
    };
    assert_eq!(
        ctx.env.events().all().filter_by_contract(&ctx.id),
        std::vec![revocada.to_xdr(&ctx.env, &ctx.id)]
    );

    let nuevo = Address::generate(&ctx.env);
    ctx.cliente().cambiar_admin(&nuevo);
    let cambiado = AdminCambiado {
        anterior: ctx.admin.clone(),
        nuevo,
    };
    assert_eq!(
        ctx.env.events().all().filter_by_contract(&ctx.id),
        std::vec![cambiado.to_xdr(&ctx.env, &ctx.id)]
    );
}

#[test]
fn un_intento_fallido_no_deja_evento() {
    let ctx = Contexto::nuevo();
    ctx.env.mock_all_auths();
    let _ = ctx
        .cliente()
        .try_publicar(&ctx.hash(1), &ctx.hash(2), &ctx.hash(3), &1, &FIN, &INICIO);
    assert!(ctx
        .env
        .events()
        .all()
        .filter_by_contract(&ctx.id)
        .events()
        .is_empty());
}

// ---------------------------------------------------------------------------------------------
// TTL

#[test]
fn constantes_de_ttl_coherentes() {
    // El umbral menor que la extensión se comprueba al compilar (lib.rs). Aquí: ambas extensiones caben
    // en el TTL máximo que cita la guía "Storage strategies" (3,110,400 ledgers) y los valores no
    // cambian sin querer.
    const TTL_MAXIMO_CITADO: u32 = 3_110_400;
    const { assert!(EXTENSION_TTL_ATESTACION <= TTL_MAXIMO_CITADO) };
    const { assert!(EXTENSION_TTL_INSTANCIA <= TTL_MAXIMO_CITADO) };
    assert_eq!(EXTENSION_TTL_ATESTACION, 2_073_600);
    assert_eq!(UMBRAL_TTL_ATESTACION, 2_056_320);
}

#[test]
fn ttl_extendido_al_publicar_y_al_consultar() {
    let ctx = Contexto::nuevo();
    ctx.env.mock_all_auths();
    publicar_ejemplo(&ctx);

    // Al publicar: la atestación y la instancia quedan con el TTL de extensión.
    assert_eq!(ctx.ttl_atestacion(&ctx.hash(1)), EXTENSION_TTL_ATESTACION);
    assert_eq!(ctx.ttl_instancia(), EXTENSION_TTL_INSTANCIA);

    // Pasan pocos ledgers: el TTL sigue arriba del umbral y consultar no lo cambia.
    let inicio = ctx.env.ledger().sequence();
    ctx.env.ledger().set_sequence_number(inicio + 100);
    ctx.cliente().consultar(&ctx.hash(1));
    assert_eq!(
        ctx.ttl_atestacion(&ctx.hash(1)),
        EXTENSION_TTL_ATESTACION - 100
    );

    // Pasan dos días: el TTL baja del umbral y consultar lo vuelve a extender.
    ctx.env
        .ledger()
        .set_sequence_number(inicio + 2 * DIA_EN_LEDGERS);
    assert!(ctx.ttl_atestacion(&ctx.hash(1)) <= UMBRAL_TTL_ATESTACION);
    assert!(ctx.ttl_instancia() <= UMBRAL_TTL_INSTANCIA);
    ctx.cliente().consultar(&ctx.hash(1));
    assert_eq!(ctx.ttl_atestacion(&ctx.hash(1)), EXTENSION_TTL_ATESTACION);
    assert_eq!(ctx.ttl_instancia(), EXTENSION_TTL_INSTANCIA);
}

#[test]
fn ttl_extendido_al_revocar() {
    let ctx = Contexto::nuevo();
    ctx.env.mock_all_auths();
    publicar_ejemplo(&ctx);
    let inicio = ctx.env.ledger().sequence();
    ctx.env
        .ledger()
        .set_sequence_number(inicio + 3 * DIA_EN_LEDGERS);
    ctx.cliente().revocar(&ctx.hash(1));
    assert_eq!(ctx.ttl_atestacion(&ctx.hash(1)), EXTENSION_TTL_ATESTACION);
}

// ---------------------------------------------------------------------------------------------
// admin y version

#[test]
fn el_constructor_no_se_puede_volver_a_llamar() {
    // El admin se fija una sola vez: después del despliegue, el host rechaza llamar a `__constructor`.
    let ctx = Contexto::nuevo();
    ctx.env.mock_all_auths();
    let otro = Address::generate(&ctx.env);
    let resultado = ctx.env.try_invoke_contract::<(), soroban_sdk::Error>(
        &ctx.id,
        &soroban_sdk::Symbol::new(&ctx.env, "__constructor"),
        (otro,).into_val(&ctx.env),
    );
    assert_eq!(
        resultado,
        Err(Ok(soroban_sdk::Error::from_type_and_code(
            xdr::ScErrorType::Context,
            xdr::ScErrorCode::InvalidAction
        )))
    );
    assert_eq!(ctx.cliente().admin(), ctx.admin);
}

#[test]
fn admin_y_version() {
    let ctx = Contexto::nuevo();
    assert_eq!(ctx.cliente().admin(), ctx.admin);
    assert_eq!(ctx.cliente().version(), VERSION);
    assert_eq!(VERSION, 1);
}

// ---------------------------------------------------------------------------------------------
// Criterio 3: la interfaz pública solo usa BytesN<32>, u32, u64, bool y Address

/// Las funciones públicas del contrato. La prueba `lista_de_funciones_completa` comprueba contra el
/// código fuente que no falte ninguna.
fn specs_de_funciones() -> StdVec<(&'static str, xdr::ScSpecEntry)> {
    let decodificar =
        |bytes: &[u8]| xdr::ScSpecEntry::from_xdr(bytes, xdr::Limits::none()).unwrap();
    std::vec![
        (
            "__constructor",
            decodificar(&Atestaciones::spec_xdr___constructor())
        ),
        ("publicar", decodificar(&Atestaciones::spec_xdr_publicar())),
        (
            "consultar",
            decodificar(&Atestaciones::spec_xdr_consultar())
        ),
        ("revocar", decodificar(&Atestaciones::spec_xdr_revocar())),
        (
            "cambiar_admin",
            decodificar(&Atestaciones::spec_xdr_cambiar_admin())
        ),
        ("admin", decodificar(&Atestaciones::spec_xdr_admin())),
        ("version", decodificar(&Atestaciones::spec_xdr_version())),
    ]
}

fn tipo_permitido(tipo: &xdr::ScSpecTypeDef) -> bool {
    match tipo {
        xdr::ScSpecTypeDef::U32
        | xdr::ScSpecTypeDef::U64
        | xdr::ScSpecTypeDef::Bool
        | xdr::ScSpecTypeDef::Address => true,
        xdr::ScSpecTypeDef::BytesN(b) => b.n == 32,
        _ => false,
    }
}

fn es_udt(tipo: &xdr::ScSpecTypeDef, nombre: &str) -> bool {
    matches!(tipo, xdr::ScSpecTypeDef::Udt(u) if u.name.to_utf8_string_lossy() == nombre)
}

#[test]
fn interfaz_publica_solo_con_tipos_permitidos() {
    for (nombre, entrada) in specs_de_funciones() {
        let xdr::ScSpecEntry::FunctionV0(funcion) = entrada else {
            panic!("{nombre}: el spec no es de una función");
        };
        assert_eq!(funcion.name.to_utf8_string_lossy(), nombre);
        // Parámetros: solo los cinco tipos permitidos.
        for parametro in funcion.inputs.iter() {
            assert!(
                tipo_permitido(&parametro.type_),
                "{nombre}({}): tipo no permitido {:?}",
                parametro.name.to_utf8_string_lossy(),
                parametro.type_
            );
        }
        // Salidas: nada, un tipo permitido, Option<Atestacion> o Result<(), error del contrato> (la
        // macro escribe el error como el tipo genérico `Error` del spec; sus códigos están en la
        // entrada UdtErrorEnumV0 de `Error`).
        for salida in funcion.outputs.iter() {
            let valida = tipo_permitido(salida)
                || matches!(salida, xdr::ScSpecTypeDef::Option(o) if es_udt(&o.value_type, "Atestacion"))
                || matches!(salida, xdr::ScSpecTypeDef::Result(r)
                    if matches!(*r.ok_type, xdr::ScSpecTypeDef::Void)
                        && matches!(*r.error_type, xdr::ScSpecTypeDef::Error));
            assert!(valida, "{nombre}: salida no permitida {salida:?}");
        }
    }

    // Los campos de la atestación: los mismos cinco tipos.
    let xdr::ScSpecEntry::UdtStructV0(estructura) =
        xdr::ScSpecEntry::from_xdr(Atestacion::spec_xdr(), xdr::Limits::none()).unwrap()
    else {
        panic!("Atestacion no es una estructura");
    };
    let campos: StdVec<String> = estructura
        .fields
        .iter()
        .map(|c| c.name.to_utf8_string_lossy())
        .collect();
    assert_eq!(
        campos,
        [
            "hash_datos",
            "hash_metodologia",
            "publicada_en",
            "publicador",
            "revocada",
            "ventana_fin",
            "ventana_inicio",
            "version_metodologia"
        ]
    );
    for campo in estructura.fields.iter() {
        assert!(tipo_permitido(&campo.type_), "campo {:?}", campo.name);
    }

    // Los eventos: los mismos cinco tipos.
    for bytes in [
        &AtestacionPublicada::spec_xdr()[..],
        &AtestacionRevocada::spec_xdr()[..],
        &AdminCambiado::spec_xdr()[..],
    ] {
        let xdr::ScSpecEntry::EventV0(evento) =
            xdr::ScSpecEntry::from_xdr(bytes, xdr::Limits::none()).unwrap()
        else {
            panic!("no es un evento");
        };
        for parametro in evento.params.iter() {
            assert!(
                tipo_permitido(&parametro.type_),
                "evento {:?}: {:?}",
                evento.name,
                parametro.name
            );
        }
    }
}

#[test]
fn lista_de_funciones_completa() {
    // Toma las `pub fn` del bloque `#[contractimpl]` del código fuente y las compara con la lista que
    // revisa `interfaz_publica_solo_con_tipos_permitidos`: una función nueva no puede escaparse.
    let fuente = include_str!("lib.rs");
    let desde = fuente
        .find("#[contractimpl]")
        .expect("falta #[contractimpl]");
    let bloque = &fuente[desde..];
    let hasta = bloque.find("\n}\n").expect("fin del bloque impl");
    let bloque = &bloque[..hasta];
    let mut en_fuente: StdVec<&str> = bloque
        .match_indices("pub fn ")
        .map(|(i, _)| {
            let resto = &bloque[i + "pub fn ".len()..];
            &resto[..resto.find('(').unwrap()]
        })
        .collect();
    let mut revisadas: StdVec<&str> = specs_de_funciones().iter().map(|(n, _)| *n).collect();
    en_fuente.sort();
    revisadas.sort();
    assert_eq!(en_fuente, revisadas);
    assert_eq!(
        fuente.matches("#[contractimpl]").count(),
        1,
        "un solo bloque #[contractimpl]"
    );
}

#[test]
fn codigos_de_error_estables() {
    assert_eq!(Error::YaExiste as u32, 1);
    assert_eq!(Error::VentanaInvalida as u32, 2);
    assert_eq!(Error::NoExiste as u32, 3);
    assert_eq!(Error::YaRevocada as u32, 4);
    assert_eq!(Error::MismoAdmin as u32, 5);

    // Los mismos códigos, como los ve un cliente en el spec del contrato.
    let xdr::ScSpecEntry::UdtErrorEnumV0(errores) =
        xdr::ScSpecEntry::from_xdr(Error::spec_xdr(), xdr::Limits::none()).unwrap()
    else {
        panic!("Error no es un enum de errores en el spec");
    };
    let casos: StdVec<(String, u32)> = errores
        .cases
        .iter()
        .map(|c| (c.name.to_utf8_string_lossy(), c.value))
        .collect();
    let esperados: StdVec<(String, u32)> = [
        ("YaExiste", 1),
        ("VentanaInvalida", 2),
        ("NoExiste", 3),
        ("YaRevocada", 4),
        ("MismoAdmin", 5),
    ]
    .iter()
    .map(|(n, v)| (String::from(*n), *v))
    .collect();
    assert_eq!(casos, esperados);
}
