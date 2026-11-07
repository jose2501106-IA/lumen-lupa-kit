//! Revisa el WASM compilado, no el código fuente: lee su spec (`contractspecv0`) y sus metadatos
//! (`contractmetav0`) con el decodificador XDR que trae soroban-sdk.
//!
//! Con soroban-sdk ≥ 28 el spec que deja `cargo build` todavía no está "sacudido": trae entradas de
//! tipos internos del SDK que el contrato no usa. `stellar contract build` (stellar-cli ≥ 25.2.0) las
//! quita después de compilar. Esta prueba aplica la misma regla que el CLI
//! (`soroban_spec::shaking::filter`, soroban-spec 29.0.1, contratos con `rssdk_spec_shaking=2`): las
//! funciones siempre se quedan y cualquier otra entrada solo si su marcador `SpEcV1` + sha256[..8]
//! está en la sección de datos. Sobre lo que se queda, comprueba:
//!
//! 1. La interfaz pública: exactamente las 7 funciones esperadas y, en cada parámetro, solo
//!    `BytesN<32>`, `u32`, `u64`, `bool` o `Address` (criterio 3 de T7); lo mismo en los campos de
//!    `Atestacion` y de los eventos. Los únicos tipos propios son `Atestacion`, `Error` y los 3 eventos.
//! 2. Que ninguna entrada propia del contrato está entre las que quitaría el CLI.
//! 3. Metadatos: soroban-sdk 29.0.1 y modelo de shaking 2.
//! 4. Tamaño menor que 128 KB, el límite de mainnet que cita la guía "Hello World" de Stellar
//!    (https://developers.stellar.org/docs/build/smart-contracts/getting-started/hello-world).
//!
//! Imprime las firmas y lo que quitaría el CLI para el log de CI. No corre con un `cargo test` normal
//! (no hay WASM en el host). En CI, después de compilar:
//!   WASM_ATESTACIONES=target/wasm32v1-none/release/lumen_lupa_atestaciones.wasm \
//!     cargo test --locked --test wasm -- --ignored --nocapture

use std::collections::{BTreeMap, BTreeSet};

use soroban_sdk::{
    xdr::{self, ReadXdr, WriteXdr},
    Bytes, Env,
};

/// Secciones del WASM que interesan aquí.
struct Secciones {
    personalizadas: Vec<(String, Vec<u8>)>,
    datos: Vec<u8>,
}

fn leer_leb128(bytes: &[u8], pos: &mut usize) -> usize {
    let mut valor: usize = 0;
    let mut corrimiento = 0;
    loop {
        let byte = bytes[*pos];
        *pos += 1;
        valor |= ((byte & 0x7f) as usize) << corrimiento;
        if byte & 0x80 == 0 {
            return valor;
        }
        corrimiento += 7;
        assert!(corrimiento < 35, "LEB128 demasiado largo");
    }
}

fn leer_secciones(wasm: &[u8]) -> Secciones {
    assert_eq!(&wasm[..4], b"\0asm", "no es un WASM");
    assert_eq!(&wasm[4..8], &[1, 0, 0, 0], "versión de WASM inesperada");
    let mut secciones = Secciones {
        personalizadas: Vec::new(),
        datos: Vec::new(),
    };
    let mut pos = 8;
    while pos < wasm.len() {
        let id = wasm[pos];
        pos += 1;
        let tamano = leer_leb128(wasm, &mut pos);
        let contenido = &wasm[pos..pos + tamano];
        match id {
            0 => {
                let mut p = 0;
                let largo = leer_leb128(contenido, &mut p);
                let nombre = String::from_utf8(contenido[p..p + largo].to_vec()).unwrap();
                secciones
                    .personalizadas
                    .push((nombre, contenido[p + largo..].to_vec()));
            }
            // Sección de datos: basta el contenido completo para buscar los marcadores.
            11 => secciones.datos.extend_from_slice(contenido),
            _ => {}
        }
        pos += tamano;
    }
    secciones
}

fn seccion(secciones: &Secciones, nombre: &str) -> Vec<u8> {
    let partes: Vec<&Vec<u8>> = secciones
        .personalizadas
        .iter()
        .filter(|(n, _)| n == nombre)
        .map(|(_, c)| c)
        .collect();
    assert!(!partes.is_empty(), "falta la sección {nombre}");
    partes.into_iter().flatten().copied().collect()
}

fn decodificar_todas<T: ReadXdr>(bytes: &[u8]) -> Vec<T> {
    let mut lector = xdr::Limited::new(std::io::Cursor::new(bytes), xdr::Limits::none());
    T::read_xdr_iter(&mut lector)
        .collect::<Result<Vec<T>, _>>()
        .unwrap()
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

fn nombre_de_tipo(tipo: &xdr::ScSpecTypeDef) -> String {
    match tipo {
        xdr::ScSpecTypeDef::BytesN(b) => format!("BytesN<{}>", b.n),
        xdr::ScSpecTypeDef::Option(o) => format!("Option<{}>", nombre_de_tipo(&o.value_type)),
        xdr::ScSpecTypeDef::Result(r) => format!(
            "Result<{}, {}>",
            nombre_de_tipo(&r.ok_type),
            nombre_de_tipo(&r.error_type)
        ),
        xdr::ScSpecTypeDef::Udt(u) => u.name.to_utf8_string_lossy(),
        xdr::ScSpecTypeDef::Void => "()".to_string(),
        otro => format!("{otro:?}").to_lowercase(),
    }
}

/// Nombre y clase de una entrada del spec.
fn describir(entrada: &xdr::ScSpecEntry) -> (String, &'static str) {
    match entrada {
        xdr::ScSpecEntry::FunctionV0(f) => (f.name.to_utf8_string_lossy(), "función"),
        xdr::ScSpecEntry::UdtStructV0(s) => (s.name.to_utf8_string_lossy(), "struct"),
        xdr::ScSpecEntry::UdtUnionV0(u) => (u.name.to_utf8_string_lossy(), "union"),
        xdr::ScSpecEntry::UdtEnumV0(e) => (e.name.to_utf8_string_lossy(), "enum"),
        xdr::ScSpecEntry::UdtErrorEnumV0(e) => (e.name.to_utf8_string_lossy(), "errores"),
        xdr::ScSpecEntry::EventV0(e) => (e.name.to_utf8_string_lossy(), "evento"),
    }
}

fn leer_wasm() -> Vec<u8> {
    let ruta = std::env::var("WASM_ATESTACIONES")
        .expect("define WASM_ATESTACIONES con la ruta del .wasm compilado");
    std::fs::read(&ruta).unwrap_or_else(|e| panic!("no pude leer {ruta}: {e}"))
}

fn conjunto(nombres: &[&str]) -> BTreeSet<String> {
    nombres.iter().map(|s| s.to_string()).collect()
}

fn lista_de_tipos<'a>(campos: impl Iterator<Item = (String, &'a xdr::ScSpecTypeDef)>) -> String {
    campos
        .map(|(nombre, tipo)| format!("{nombre}: {}", nombre_de_tipo(tipo)))
        .collect::<Vec<_>>()
        .join(", ")
}

#[test]
#[ignore = "necesita el WASM compilado (WASM_ATESTACIONES); corre en CI"]
fn wasm_interfaz_spec_y_metadatos() {
    let wasm = leer_wasm();
    let secciones = leer_secciones(&wasm);
    println!("Tamaño del WASM: {} bytes", wasm.len());
    assert!(wasm.len() < 128 * 1024, "el WASM pasa de 128 KB");

    // --- Lo que dejaría `stellar contract build` -------------------------------------------------
    let entradas: Vec<xdr::ScSpecEntry> = decodificar_todas(&seccion(&secciones, "contractspecv0"));
    let env = Env::default();
    env.cost_estimate().budget().reset_unlimited();
    let tiene_marcador = |entrada: &xdr::ScSpecEntry| {
        let bytes = entrada.to_xdr(xdr::Limits::none()).unwrap();
        let hash = env
            .crypto()
            .sha256(&Bytes::from_slice(&env, &bytes))
            .to_array();
        let mut marcador = b"SpEcV1".to_vec();
        marcador.extend_from_slice(&hash[..8]);
        secciones
            .datos
            .windows(marcador.len())
            .any(|w| w == marcador.as_slice())
    };
    let (quedan, se_quitan): (Vec<&xdr::ScSpecEntry>, Vec<&xdr::ScSpecEntry>) = entradas
        .iter()
        .partition(|e| matches!(e, xdr::ScSpecEntry::FunctionV0(_)) || tiene_marcador(e));

    // --- 1. Interfaz pública -------------------------------------------------------------------------
    let mut por_clase: BTreeMap<&str, BTreeSet<String>> = BTreeMap::new();
    println!("Interfaz pública (spec del WASM, lo que deja el CLI):");
    for entrada in &quedan {
        let (nombre, clase) = describir(entrada);
        por_clase.entry(clase).or_default().insert(nombre.clone());
        match entrada {
            xdr::ScSpecEntry::FunctionV0(f) => {
                let salida = f
                    .outputs
                    .iter()
                    .map(nombre_de_tipo)
                    .next()
                    .unwrap_or_else(|| "()".to_string());
                let parametros = lista_de_tipos(
                    f.inputs
                        .iter()
                        .map(|p| (p.name.to_utf8_string_lossy(), &p.type_)),
                );
                println!("  fn {nombre}({parametros}) -> {salida}");
                for p in f.inputs.iter() {
                    assert!(
                        tipo_permitido(&p.type_),
                        "{nombre}: parámetro {} de tipo no permitido {:?}",
                        p.name.to_utf8_string_lossy(),
                        p.type_
                    );
                }
            }
            xdr::ScSpecEntry::UdtStructV0(s) => {
                let campos = lista_de_tipos(
                    s.fields
                        .iter()
                        .map(|c| (c.name.to_utf8_string_lossy(), &c.type_)),
                );
                println!("  struct {nombre} {{ {campos} }}");
                for c in s.fields.iter() {
                    assert!(
                        tipo_permitido(&c.type_),
                        "campo {:?} de tipo no permitido",
                        c.name
                    );
                }
            }
            xdr::ScSpecEntry::EventV0(e) => {
                let parametros = lista_de_tipos(
                    e.params
                        .iter()
                        .map(|p| (p.name.to_utf8_string_lossy(), &p.type_)),
                );
                println!("  evento {nombre} {{ {parametros} }}");
                for p in e.params.iter() {
                    assert!(
                        tipo_permitido(&p.type_),
                        "evento {:?}: {:?}",
                        e.name,
                        p.name
                    );
                }
            }
            xdr::ScSpecEntry::UdtErrorEnumV0(e) => {
                let casos: Vec<String> = e
                    .cases
                    .iter()
                    .map(|c| format!("{}={}", c.name.to_utf8_string_lossy(), c.value))
                    .collect();
                println!("  errores {nombre}: {}", casos.join(", "));
            }
            otra => panic!("entrada propia inesperada en el spec: {otra:?}"),
        }
    }
    let clase = |c: &str| por_clase.get(c).cloned().unwrap_or_default();
    assert_eq!(
        clase("función"),
        conjunto(&[
            "__constructor",
            "publicar",
            "consultar",
            "revocar",
            "cambiar_admin",
            "admin",
            "version",
        ])
    );
    assert_eq!(clase("struct"), conjunto(&["Atestacion"]));
    assert_eq!(
        clase("evento"),
        conjunto(&["AtestacionPublicada", "AtestacionRevocada", "AdminCambiado"])
    );
    assert_eq!(clase("errores"), conjunto(&["Error"]));
    assert_eq!(
        por_clase.keys().copied().collect::<BTreeSet<&str>>(),
        ["errores", "evento", "función", "struct"]
            .into_iter()
            .collect()
    );

    // --- 2. Lo que quitaría el CLI no es del contrato --------------------------------------------
    let propios = conjunto(&[
        "Atestacion",
        "Error",
        "AtestacionPublicada",
        "AtestacionRevocada",
        "AdminCambiado",
    ]);
    let mut quitadas = Vec::new();
    for entrada in &se_quitan {
        let (nombre, clase) = describir(entrada);
        assert!(
            !propios.contains(&nombre),
            "el CLI quitaría {nombre}, que es del contrato"
        );
        quitadas.push(format!("{clase} {nombre}"));
    }
    println!(
        "Spec del WASM de cargo: {} entradas; `stellar contract build` quitaría {} sin uso, del SDK: {}",
        entradas.len(),
        se_quitan.len(),
        quitadas.join(", ")
    );

    // --- 3. Metadatos ---------------------------------------------------------------------------
    let meta: Vec<xdr::ScMetaEntry> = decodificar_todas(&seccion(&secciones, "contractmetav0"));
    let mut valores = BTreeMap::new();
    for xdr::ScMetaEntry::ScMetaV0(m) in &meta {
        valores.insert(m.key.to_utf8_string_lossy(), m.val.to_utf8_string_lossy());
    }
    println!("Metadatos: {valores:?}");
    assert_eq!(
        valores.get("rssdk_spec_shaking").map(String::as_str),
        Some("2")
    );
    assert!(valores
        .get("rssdkver")
        .is_some_and(|v| v.starts_with("29.0.1")));
}
