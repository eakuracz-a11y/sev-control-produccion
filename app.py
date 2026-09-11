
# ============================================================
# SEV | CONTROL DE PRODUCCIÓN
# V1.0
# ============================================================
# Streamlit + SQLite
# Gestión de órdenes internas, lotes y cantidades producidas.
# ============================================================

from __future__ import annotations

import sqlite3
import calendar
import smtplib
import base64
import gzip
import os
import tempfile
import requests
from email.message import EmailMessage
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import quote

import numpy as np
import pandas as pd
import streamlit as st


# ============================================================
# CONFIGURACIÓN
# ============================================================

APP_VERSION = "V1.15"
APP_TITLE = "SEV | Control de Producción"


BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "sev_produccion.db"

# ============================================================
# PERSISTENCIA V1.15
# ============================================================
# La aplicación mantiene SQLite para no modificar toda la lógica
# existente, pero crea/restaura un backup comprimido en un
# repositorio GitHub privado.
#
# Secrets requeridos en Streamlit:
# GITHUB_TOKEN = "github_pat_..."
# GITHUB_REPO = "eakuracz-a11y/sev-control-produccion"
# GITHUB_BRANCH = "main"                    # opcional
# GITHUB_DB_BACKUP_PATH = "data/sev_produccion.db.gz"  # opcional
# ============================================================

GITHUB_TOKEN = str(st.secrets.get("GITHUB_TOKEN", "")).strip()
GITHUB_REPO = str(
    st.secrets.get(
        "GITHUB_REPO",
        "eakuracz-a11y/sev-control-produccion",
    )
).strip()
GITHUB_BRANCH = str(st.secrets.get("GITHUB_BRANCH", "main")).strip()
GITHUB_DB_BACKUP_PATH = str(
    st.secrets.get(
        "GITHUB_DB_BACKUP_PATH",
        "data/sev_produccion.db.gz",
    )
).strip()

PERSISTENCE_ENABLED = bool(GITHUB_TOKEN and GITHUB_REPO)

def _github_headers():
    return {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }

def _github_contents_url():
    return (
        f"https://api.github.com/repos/{GITHUB_REPO}/contents/"
        f"{GITHUB_DB_BACKUP_PATH}"
    )

def remote_backup_info():
    """Devuelve metadata del backup remoto o None si no existe/no está disponible."""
    if not PERSISTENCE_ENABLED:
        return None
    try:
        r = requests.get(
            _github_contents_url(),
            headers=_github_headers(),
            params={"ref": GITHUB_BRANCH},
            timeout=20,
        )
        if r.status_code == 200:
            return r.json()
        return None
    except Exception:
        return None

def restore_db_from_github(force=False):
    """
    Restaura la base SQLite desde GitHub.
    - Automáticamente solo cuando DB_PATH no existe o está vacío.
    - force=True permite recuperación manual desde el módulo Backup.
    """
    if not PERSISTENCE_ENABLED:
        return False, "Persistencia GitHub no configurada."

    if not force and DB_PATH.exists() and DB_PATH.stat().st_size > 0:
        return False, "La base local ya existe; no se reemplazó."

    try:
        info = remote_backup_info()
        if not info:
            return False, "No existe un backup remoto todavía."

        download_url = info.get("download_url")
        if not download_url:
            return False, "GitHub no devolvió URL de descarga del backup."

        r = requests.get(
            download_url,
            headers=_github_headers(),
            timeout=30,
        )
        r.raise_for_status()

        raw = gzip.decompress(r.content)

        # Validar en archivo temporal antes de reemplazar.
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
            tmp.write(raw)
            tmp_path = Path(tmp.name)

        try:
            conn = sqlite3.connect(tmp_path)
            required = {"ordenes", "productos", "familias"}
            found = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            conn.close()

            missing = required - found
            if missing:
                return False, (
                    "El backup remoto no parece una base SEV válida. "
                    f"Faltan tablas: {', '.join(sorted(missing))}"
                )

            DB_PATH.write_bytes(tmp_path.read_bytes())
        finally:
            try:
                tmp_path.unlink(missing_ok=True)
            except Exception:
                pass

        return True, "Base restaurada desde el backup remoto."
    except Exception as exc:
        return False, f"No se pudo restaurar el backup: {exc}"

def backup_db_to_github(reason="actualización"):
    """
    Comprime y guarda la base en GitHub.
    El error de backup NUNCA anula una operación de producción ya guardada.
    """
    if not PERSISTENCE_ENABLED:
        return False, "Persistencia GitHub no configurada."
    if not DB_PATH.exists() or DB_PATH.stat().st_size == 0:
        return False, "La base local está vacía."

    try:
        # Cerrar/volcar WAL no es necesario porque la app usa conexiones cortas
        # y commits explícitos. Se comprime el archivo ya confirmado.
        compressed = gzip.compress(DB_PATH.read_bytes(), compresslevel=9)
        encoded = base64.b64encode(compressed).decode("ascii")

        current = remote_backup_info()
        payload = {
            "message": f"SEV Producción DB backup · {reason} · "
                       f"{datetime.now().isoformat(timespec='seconds')}",
            "content": encoded,
            "branch": GITHUB_BRANCH,
        }
        if current and current.get("sha"):
            payload["sha"] = current["sha"]

        r = requests.put(
            _github_contents_url(),
            headers=_github_headers(),
            json=payload,
            timeout=45,
        )

        if r.status_code not in (200, 201):
            msg = r.text[:500]
            return False, f"GitHub respondió {r.status_code}: {msg}"

        st.session_state["last_backup_ok"] = datetime.now().isoformat(
            timespec="seconds"
        )
        st.session_state["last_backup_error"] = ""
        return True, "Backup persistente actualizado."
    except Exception as exc:
        st.session_state["last_backup_error"] = str(exc)
        return False, f"No se pudo crear backup: {exc}"

def validate_uploaded_sqlite(raw_bytes):
    """Valida una base SQLite antes de permitir restauración manual."""
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
            tmp.write(raw_bytes)
            tmp_path = Path(tmp.name)

        conn = sqlite3.connect(tmp_path)
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        counts = {}
        for name in [
            "ordenes",
            "familias",
            "productos",
            "materias_primas",
            "formulaciones",
            "personas",
        ]:
            if name in tables:
                counts[name] = int(
                    conn.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]
                )
        conn.close()

        required = {"ordenes", "familias", "productos"}
        missing = required - tables
        if missing:
            return False, counts, (
                "No es una base SEV válida. Faltan: "
                + ", ".join(sorted(missing))
            )
        return True, counts, "Base SQLite válida."
    except Exception as exc:
        return False, {}, f"No se pudo validar la base: {exc}"
    finally:
        if tmp_path:
            try:
                tmp_path.unlink(missing_ok=True)
            except Exception:
                pass

# En cada arranque de un contenedor nuevo, recuperar primero la última copia.
_restore_ok, _restore_msg = restore_db_from_github(force=False)

DEFAULT_FAMILIES = [
    ("ADJ", "Adyuvantes"),
    ("FER", "Fertilizantes"),
    ("BIO", "Biológicos"),
    ("FUN", "Fungicidas"),
]

UNIDADES = ["L", "kg", "unidades"]

SEV_PERSONAS = [
    {
        "nombre": "Alejandro Kuracz",
        "email": "kuraczg7@gmail.com",
        "rol": "Administrador",
    },
    {
        "nombre": "Flavia Guedes",
        "email": "flavia.guedes@sevion.com.br",
        "rol": "Responsable",
    },
    {
        "nombre": "Bruno Maia",
        "email": "bruno.maia@sevion.com.br",
        "rol": "Responsable",
    },
    {
        "nombre": "Camille Maia",
        "email": "camille.maia@sevion.com.br",
        "rol": "Responsable",
    },
    {
        "nombre": "Ana Nolasco",
        "email": "ana.nolasco@sevion.com.br",
        "rol": "Responsable",
    },
]

DEFAULT_SHELF_LIFE_MONTHS = 24

# ============================================================
# FORMULACIÓN MAESTRA INICIAL · ADJUVANTE G - STEPAN
# Fuente: formulación proporcionada por el usuario.
# Base: 274,000 kg ≈ 300,246 L
# Densidad teórica final: 0,913 kg/L
# ============================================================

DEFAULT_ADJ_G_FORMULA = {
    "familia_codigo": "ADJ",
    "producto": "Adjuvante G",
    "version": 1,
    "cantidad_base": 274.000,
    "unidad_base": "kg",
    "volumen_base_l": 300.246,
    "densidad_teorica": 0.913,
    "observaciones": "Formulação G Surfactante - STEPAN",
    "items": [
        {
            "codigo": "II0030",
            "nombre": "ESTER METILICO DE OLEO VEGETAL",
            "densidad": 0.879,
            "cantidad_kg": 220.000,
            "cantidad_l": 250.280,
            "porcentaje_mm": 80.0,
        },
        {
            "codigo": "IA0002",
            "nombre": "STEPGROW EP ME",
            "densidad": 1.011,
            "cantidad_kg": 27.000,
            "cantidad_l": 24.000,
            "porcentaje_mm": 10.0,
        },
        {
            "codigo": "IA0006",
            "nombre": "STEPGROW SRA-2",
            "densidad": 1.040,
            "cantidad_kg": 27.000,
            "cantidad_l": 25.960,
            "porcentaje_mm": 10.0,
        },
    ],
}

# Consumibles/embalajes cargados desde listado SIGA/MATA225 del 10/09/2026.
DEFAULT_CONSUMABLES = [
    ("EM0001", 'BOMBANA BXS10/63 550G BRANCA (PALETIZADA)', "01", "Embalaje", '-500,0000'),
    ("EM0003", 'BOMBANA BXS20G3/63 1100G BRANCA PALETIZADA', "01", "Embalaje", '-50,0000'),
    ("EM0005", 'TAMPA 63MM C/ SELO DE ALUMINIO VENTILADO SIMPLES (GD)', "01", "Embalaje", '-550,0000'),
    ("EM0007", 'CONTEINER IBC 1000 LITROS', "01", "Embalaje", '-1,4480'),
    ("MC0001", 'GRAMA ESMERALDA METRO', "01", "Consumible", '0,0000'),
    ("MC0002", 'VERGALHAO CA-60 5.0 X 12MT', "01", "Consumible", '0,0000'),
    ("MC0019", 'DIESEL S500 COMUM', "03", "Consumible", '0,0000'),
    ("MC0021", 'GASOLINA COMUM', "03", "Consumible", '0,0000'),
    ("MC0043", 'DISCO DE CORTE', "03", "Consumible", '0,0000'),
    ("MC0046", 'FILME STRECH TRANSPARENTE', "03", "Consumible", '0,0000'),
    ("MC0056", 'AGUA MINERAL S/GAS', "03", "Consumible", '0,0000'),
    ("MC0060", 'BOLETIM', "01", "Consumible", '0,0000'),
    ("MC0060", 'BOLETIM', "03", "Consumible", '0,0000'),
    ("MC0070", 'DIESEL S10', "03", "Consumible", '0,0000'),
    ("MC0071", 'MANUTENCAO PREDIAL E INSTALACOES', "03", "Consumible", '0,0000'),
    ("MC0086", 'ARRUELA LISA', "03", "Consumible", '0,0000'),
    ("MC0087", 'PARAFUSO', "03", "Consumible", '0,0000'),
    ("MC0089", 'AGUA MINERAL C/GAS', "01", "Consumible", '0,0000'),
    ("MC0089", 'AGUA MINERAL C/GAS', "03", "Consumible", '0,0000'),
    ("MC0094", 'MICROONDAS', "01", "Consumible", '0,0000'),
    ("MC0094", 'MICROONDAS', "03", "Consumible", '0,0000'),
    ("MC0095", 'CURVA INOX', "03", "Consumible", '0,0000'),
    ("MC0096", 'PONTA ROSCADA INOX', "03", "Consumible", '0,0000'),
    ("MC0097", 'CANALETA  ADESIVA', "03", "Consumible", '0,0000'),
    ("MC0098", 'CABO FLEX', "03", "Consumible", '0,0000'),
    ("MC0099", 'METALON', "03", "Consumible", '0,0000'),
    ("MC0100", 'RODIZIO PRETO GIRATORIO', "03", "Consumible", '0,0000'),
    ("MC0101", 'LICENCIAMENTO DE E-MAIL', "03", "Consumible", '0,0000'),
    ("MC0102", 'ARGONIO', "03", "Consumible", '0,0000'),
    ("MC0103", 'BROCA', "03", "Consumible", '0,0000'),
    ("MC0104", 'ESCOVA CIRCULAR TRANCADA', "03", "Consumible", '0,0000'),
    ("MC0105", 'DISCO FLAP', "03", "Consumible", '0,0000'),
    ("MC0106", 'VARETA', "03", "Consumible", '0,0000'),
    ("MC0107", 'BARRA ROSCADA', "03", "Consumible", '0,0000'),
    ("MC0108", 'PORCA', "03", "Consumible", '0,0000'),
    ("MC0109", 'ELETRODO PH SENSOGLASS', "03", "Consumible", '0,0000'),
    ("MC0110", 'COPO PLASTICO PARA ANALISE DE SOLO', "03", "Consumible", '0,0000'),
    ("MC0111", 'TAMPA PARA COPO DE SOLO', "03", "Consumible", '0,0000'),
    ("MC0112", 'CARBONATO DE SODIO', "03", "Consumible", '0,0000'),
    ("MC0113", 'PANO MULTIUSO', "03", "Consumible", '0,0000'),
    ("MC0114", 'COLUNA DEIONIZADORA', "03", "Consumible", '0,0000'),
    ("MC0115", 'PRONTUARIO', "03", "Consumible", '0,0000'),
    ("MC0116", 'CAPA TOPFUSION', "03", "Consumible", '0,0000'),
    ("MC0117", 'TE REDUCAO TOPFUSION', "03", "Consumible", '0,0000'),
    ("MC0118", 'JOELHO TOPFUSION', "03", "Consumible", '0,0000'),
    ("MC0119", 'CONEXAO RETA MACHO', "03", "Consumible", '0,0000'),
    ("MC0120", 'SUPORTE DESLIZANTE', "03", "Consumible", '0,0000'),
    ("MC0121", 'LUVA', "03", "Consumible", '0,0000'),
    ("MC0122", 'TUBO', "03", "Consumible", '0,0000'),
    ("MC0123", 'CONEXAO COTOVELO', "03", "Consumible", '0,0000'),
    ("MC0124", 'MANGUEIRA', "03", "Consumible", '0,0000'),
    ("MC0125", 'REGISTRO ESFERA', "03", "Consumible", '0,0000'),
    ("MC0126", 'BUCHA', "03", "Consumible", '0,0000'),
    ("MC0127", 'ABRACADEIRA', "03", "Consumible", '0,0000'),
    ("MC0128", 'PERFIL LAMINADO', "03", "Consumible", '0,0000'),
    ("MC0129", 'CHAPA', "03", "Consumible", '0,0000'),
    ("MC0130", 'ELETRODO', "03", "Consumible", '0,0000'),
    ("MC0131", 'PERFIL', "03", "Consumible", '0,0000'),
    ("MC0132", 'CURVA  ACO', "03", "Consumible", '0,0000'),
    ("MC0133", 'MANOMETRO', "03", "Consumible", '0,0000'),
    ("MC0134", 'TUBO SIFAO', "03", "Consumible", '0,0000'),
    ("MC0135", 'CAFE', "03", "Consumible", '0,0000'),
    ("MC0137", 'DETERGENTE', "03", "Consumible", '0,0000'),
    ("MC0138", 'FILTRO DE PAPEL', "03", "Consumible", '0,0000'),
    ("MC0139", 'CANTONEIRA', "03", "Consumible", '0,0000'),
    ("MC0141", 'TINTA SINTETICA', "03", "Consumible", '0,0000'),
    ("MC0142", 'PRIMER', "03", "Consumible", '0,0000'),
    ("MC0143", 'THINER', "03", "Consumible", '0,0000'),
    ("MC0144", 'JUNTA LIQUIDA', "03", "Consumible", '0,0000'),
    ("MC0145", 'BALANCA ELETRONICA', "03", "Consumible", '1,0000'),
    ("MC0146", 'LUBRAX ESSENCIAL', "03", "Consumible", '0,0000'),
    ("MC0147", 'JALECO', "03", "Consumible", '0,0000'),
    ("MC0148", 'FECHADURA SOLENOIDE', "03", "Consumible", '0,0000'),
    ("MC0149", 'BOTOEIRA INOX', "03", "Consumible", '0,0000'),
    ("MC0150", 'CONTROLE ACESSO FACIAL', "03", "Consumible", '0,0000'),
    ("MC0151", 'FONTE AUXILIAR', "03", "Consumible", '0,0000'),
    ("MC0152", 'BATERIA', "03", "Consumible", '0,0000'),
    ("MC0153", 'CABO ENERGIA PARALELO', "03", "Consumible", '0,0000'),
    ("MC0154", 'CABO HOMOLOGADO', "03", "Consumible", '0,0000'),
    ("MC0155", 'CAIXA DE PASSAGEM', "03", "Consumible", '0,0000'),
    ("MC0156", 'BOLSA 5L ESPECIAL', "03", "Consumible", '0,0000'),
    ("MC0157", 'SERINGA 20ML COM AGULHA', "03", "Consumible", '0,0000'),
    ("MC0158", 'CONECTOR MACHO', "03", "Consumible", '0,0000'),
    ("MC0159", 'COPO TERMICO', "03", "Consumible", '0,0000'),
    ("MC0160", 'COPO CAFE/CHA', "03", "Consumible", '0,0000'),
    ("MC0161", 'COPO DE PAPEL KRAFT ECO', "03", "Consumible", '0,0000'),
    ("MC0162", 'MANGUEIRA SUCCAO', "03", "Consumible", '0,0000'),
    ("MC0163", 'CAPA P/ CONEXAO', "03", "Consumible", '0,0000'),
    ("MC0164", 'UNIAO RED', "03", "Consumible", '0,0000'),
    ("MC0173", 'PISTOLA', "03", "Consumible", '0,0000'),
    ("MC0174", 'ANEL DE PLATINA PARA TENSIOMETRO DST30', "03", "Consumible", '0,0000'),
    ("MC0175", 'TENSIOMETRO ANALISADR DE TENSAO SUPERFICIAL DST30', "03", "Consumible", '0,0000'),
    ("MC0176", 'TUBO ELET PVC', "03", "Consumible", '0,0000'),
    ("MC0177", 'GLP P20', "03", "Consumible", '0,0000'),
]

ESTADOS = [
    "Planificada",
    "En preparación",
    "En producción",
    "Finalizada",
    "Cancelada",
]

PRIORIDADES = [
    "Normal",
    "Alta",
    "Crítica",
]

st.set_page_config(
    page_title="SEV | Producción",
    page_icon="🏭",
    layout="wide",
    initial_sidebar_state="expanded",
)


# ============================================================
# ESTILO VISUAL · SEV
# ============================================================

st.markdown(
    """
    <style>
    :root {
        --sev-green: #0f6b4f;
        --sev-green-dark: #0a4f3b;
        --sev-green-soft: #eaf5f0;
        --sev-border: #d7e5df;
        --sev-text: #1f2937;
        --sev-muted: #6b7280;
        --sev-red: #b42318;
        --sev-amber: #b7791f;
        --sev-blue: #175cd3;
    }

    .block-container {
        max-width: 1500px;
        padding-top: 1rem;
        padding-bottom: 2rem;
    }

    h1, h2, h3 {
        color: var(--sev-green-dark);
    }

    .sev-title {
        background: linear-gradient(90deg, var(--sev-green-dark), var(--sev-green));
        color: white;
        padding: 16px 20px;
        border-radius: 14px;
        margin-bottom: 14px;
        box-shadow: 0 4px 14px rgba(15,107,79,.15);
    }

    .sev-title h1 {
        color: white !important;
        margin: 0;
        font-size: 1.8rem;
    }

    .sev-title p {
        margin: 4px 0 0 0;
        opacity: .92;
    }

    [data-testid="stMetric"] {
        background: #ffffff;
        border: 1px solid var(--sev-border);
        border-radius: 12px;
        padding: 10px 12px;
        box-shadow: 0 2px 7px rgba(16,24,40,.04);
    }

    [data-testid="stMetricValue"] {
        color: var(--sev-green-dark);
    }

    .sev-box {
        border: 1px solid var(--sev-border);
        border-left: 5px solid var(--sev-green);
        background: #fff;
        border-radius: 10px;
        padding: 12px 14px;
        margin-bottom: 10px;
    }

    .sev-lot {
        font-size: 1.15rem;
        font-weight: 700;
        color: var(--sev-green-dark);
    }

    .lot-status {
        display: inline-block;
        padding: 4px 9px;
        border-radius: 999px;
        color: white;
        font-size: .75rem;
        font-weight: 700;
        margin-left: 8px;
        vertical-align: middle;
    }

    .small-note {
        color: var(--sev-muted);
        font-size: .82rem;
    }

    div[data-testid="stDataFrame"] {
        border: 1px solid var(--sev-border);
        border-radius: 10px;
        overflow: hidden;
    }

    .stButton > button[kind="primary"] {
        background-color: var(--sev-green);
        border-color: var(--sev-green);
    }

    .stButton > button[kind="primary"]:hover {
        background-color: var(--sev-green-dark);
        border-color: var(--sev-green-dark);
    }

    @media (max-width: 800px) {
        .block-container {
            padding-left: .7rem;
            padding-right: .7rem;
        }
        .sev-title h1 {
            font-size: 1.45rem;
        }
    }
    </style>
    """,
    unsafe_allow_html=True,
)


# ============================================================
# BASE DE DATOS
# ============================================================

def get_conn():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn



MATERIAS_PRIMAS_MATA225 = [('MP0001', 'ERCANOL IT 6', '01', 0.0), ('MP0002', 'ERCALEAF MSO', '01', 0.0), ('MP0003', 'ERCALEAF ADJ ARS', '01', 0.0), ('MP0004', 'XIAMETER AFE 430 BT305', '01', 0.0), ('MP0013', 'ACIDO BORICO', '01', -5720.0), ('MP0017', 'MONOETANOLAMINA (MEA)', '01', -2960.0), ('MP0019', 'AGUA', '01', -1340.0)]


def seed_materias_primas_mata225():
    """Carga inicial idempotente de materias primas del listado MATA225."""
    conn = get_conn()
    try:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(materias_primas)").fetchall()]
        if not cols:
            return

        for codigo, nombre, almacen, saldo in MATERIAS_PRIMAS_MATA225:
            existing = conn.execute(
                "SELECT id FROM materias_primas WHERE UPPER(TRIM(nombre)) = UPPER(TRIM(?)) LIMIT 1",
                (nombre,),
            ).fetchone()

            if existing:
                material_id = int(existing[0])

                update_parts = []
                update_values = []

                if "codigo_totvs" in cols:
                    update_parts.append("codigo_totvs = ?")
                    update_values.append(codigo)
                if "codigo" in cols:
                    update_parts.append("codigo = COALESCE(NULLIF(codigo, ''), ?)")
                    update_values.append(codigo)
                if "almacen" in cols:
                    update_parts.append("almacen = ?")
                    update_values.append(almacen)
                if "saldo_actual" in cols:
                    update_parts.append("saldo_actual = ?")
                    update_values.append(float(saldo))
                if "unidad_stock" in cols:
                    update_parts.append("unidad_stock = COALESCE(NULLIF(unidad_stock, ''), 'kg')")

                if update_parts:
                    update_values.append(material_id)
                    conn.execute(
                        f"UPDATE materias_primas SET {', '.join(update_parts)} WHERE id = ?",
                        tuple(update_values),
                    )
                continue

            values = {}
            if "nombre" in cols:
                values["nombre"] = nombre
            if "unidad" in cols:
                values["unidad"] = "kg"
            if "activo" in cols:
                values["activo"] = 1
            if "created_at" in cols:
                values["created_at"] = datetime.now().isoformat(timespec="seconds")

            # Campos opcionales: se completan solo si existen en la versión actual de la BD.
            if "codigo" in cols:
                values["codigo"] = codigo
            if "codigo_totvs" in cols:
                values["codigo_totvs"] = codigo
            if "almacen" in cols:
                values["almacen"] = almacen
            if "saldo_actual" in cols:
                values["saldo_actual"] = saldo
            if "unidad_stock" in cols:
                values["unidad_stock"] = "kg"

            if values:
                field_names = list(values.keys())
                placeholders = ", ".join(["?"] * len(field_names))
                conn.execute(
                    f"INSERT INTO materias_primas ({', '.join(field_names)}) VALUES ({placeholders})",
                    tuple(values[k] for k in field_names),
                )

        conn.commit()
    finally:
        conn.close()


def init_db():
    conn = get_conn()
    cur = conn.cursor()

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS familias (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            codigo TEXT UNIQUE NOT NULL,
            nombre TEXT NOT NULL,
            activo INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS productos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nombre TEXT NOT NULL,
            linea TEXT NOT NULL,
            familia_id INTEGER,
            unidad TEXT NOT NULL DEFAULT 'L',
            activo INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            FOREIGN KEY (familia_id) REFERENCES familias(id)
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS ordenes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            orden_codigo TEXT UNIQUE NOT NULL,
            lote_codigo TEXT UNIQUE NOT NULL,
            linea TEXT NOT NULL,
            producto TEXT NOT NULL,
            cantidad_planificada REAL NOT NULL,
            cantidad_producida REAL DEFAULT 0,
            unidad TEXT NOT NULL,
            fecha_orden TEXT NOT NULL,
            fecha_inicio TEXT,
            fecha_fin TEXT,
            responsable TEXT,
            prioridad TEXT NOT NULL DEFAULT 'Normal',
            estado TEXT NOT NULL DEFAULT 'Planificada',
            observaciones TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS movimientos_produccion (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            orden_id INTEGER NOT NULL,
            fecha TEXT NOT NULL,
            cantidad REAL NOT NULL,
            observacion TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY (orden_id) REFERENCES ordenes(id)
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS eventos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            orden_id INTEGER NOT NULL,
            fecha TEXT NOT NULL,
            evento TEXT NOT NULL,
            detalle TEXT,
            FOREIGN KEY (orden_id) REFERENCES ordenes(id)
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS personas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nombre TEXT NOT NULL,
            email TEXT UNIQUE NOT NULL,
            rol TEXT NOT NULL DEFAULT 'Responsable',
            activo INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS notificaciones (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            orden_id INTEGER NOT NULL,
            destinatarios TEXT NOT NULL,
            asunto TEXT NOT NULL,
            cuerpo TEXT NOT NULL,
            estado TEXT NOT NULL DEFAULT 'Generado',
            created_at TEXT NOT NULL,
            FOREIGN KEY (orden_id) REFERENCES ordenes(id)
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS materias_primas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            codigo TEXT UNIQUE NOT NULL,
            nombre TEXT NOT NULL,
            unidad TEXT NOT NULL DEFAULT 'kg',
            activo INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL
        )
        """
    )

    cur.execute("PRAGMA table_info(materias_primas)")
    mp_columns = {row[1] for row in cur.fetchall()}

    for column_name, column_type in [
        ("codigo_totvs", "TEXT"),
        ("almacen", "TEXT"),
        ("saldo_actual", "REAL DEFAULT 0"),
        ("densidad", "REAL"),
        ("unidad_stock", "TEXT"),
    ]:
        if column_name not in mp_columns:
            cur.execute(
                f"ALTER TABLE materias_primas ADD COLUMN {column_name} {column_type}"
            )

    # Completar codigo_totvs con el código existente cuando corresponda.
    cur.execute(
        """
        UPDATE materias_primas
        SET codigo_totvs = codigo
        WHERE (codigo_totvs IS NULL OR TRIM(codigo_totvs) = '')
          AND codigo IS NOT NULL
          AND TRIM(codigo) <> ''
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS formulaciones (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            producto_id INTEGER NOT NULL,
            version INTEGER NOT NULL DEFAULT 1,
            cantidad_base REAL NOT NULL DEFAULT 100,
            unidad_base TEXT NOT NULL DEFAULT 'L',
            observaciones TEXT,
            activa INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            UNIQUE(producto_id, version),
            FOREIGN KEY (producto_id) REFERENCES productos(id)
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS formulacion_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            formulacion_id INTEGER NOT NULL,
            materia_prima_id INTEGER NOT NULL,
            cantidad REAL NOT NULL,
            unidad TEXT NOT NULL,
            observacion TEXT,
            FOREIGN KEY (formulacion_id) REFERENCES formulaciones(id),
            FOREIGN KEY (materia_prima_id) REFERENCES materias_primas(id)
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS consumibles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            codigo TEXT NOT NULL,
            descripcion TEXT NOT NULL,
            armazem TEXT,
            categoria TEXT NOT NULL DEFAULT 'Consumible',
            saldo_referencia TEXT,
            activo INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            UNIQUE(codigo, armazem)
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS consumos_consumibles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            orden_id INTEGER NOT NULL,
            consumible_id INTEGER NOT NULL,
            fecha TEXT NOT NULL,
            cantidad_real REAL NOT NULL,
            unidad TEXT NOT NULL DEFAULT 'unidades',
            lote_consumible TEXT,
            observacion TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY (orden_id) REFERENCES ordenes(id),
            FOREIGN KEY (consumible_id) REFERENCES consumibles(id)
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS reservas_materias_primas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            orden_id INTEGER NOT NULL,
            materia_prima_id INTEGER NOT NULL,
            cantidad_teorica REAL NOT NULL DEFAULT 0,
            unidad TEXT NOT NULL DEFAULT 'kg',
            updated_at TEXT NOT NULL,
            UNIQUE(orden_id, materia_prima_id),
            FOREIGN KEY (orden_id) REFERENCES ordenes(id),
            FOREIGN KEY (materia_prima_id) REFERENCES materias_primas(id)
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS consumos_materias_primas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            orden_id INTEGER NOT NULL,
            materia_prima_id INTEGER NOT NULL,
            fecha TEXT NOT NULL,
            cantidad_real REAL NOT NULL,
            unidad TEXT NOT NULL,
            lote_mp TEXT,
            observacion TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY (orden_id) REFERENCES ordenes(id),
            FOREIGN KEY (materia_prima_id) REFERENCES materias_primas(id)
        )
        """
    )

    # Migración compatible: agregar familia_id si la base ya existía.
    cur.execute("PRAGMA table_info(productos)")
    product_columns = {row[1] for row in cur.fetchall()}
    if "familia_id" not in product_columns:
        cur.execute("ALTER TABLE productos ADD COLUMN familia_id INTEGER")

    cur.execute("PRAGMA table_info(ordenes)")
    order_columns = {row[1] for row in cur.fetchall()}

    for column_name, column_type in [
        ("fecha_vencimiento", "TEXT"),
        ("vida_util_meses", "INTEGER"),
        ("responsable_email", "TEXT"),
        ("producto_id", "INTEGER"),
        ("formulacion_id", "INTEGER"),
        ("cantidad_real_producida", "REAL DEFAULT 0"),
    ]:
        if column_name not in order_columns:
            cur.execute(
                f"ALTER TABLE ordenes ADD COLUMN {column_name} {column_type}"
            )

    # Migración de formulaciones: densidad y volumen base.
    cur.execute("PRAGMA table_info(formulaciones)")
    formulation_columns = {row[1] for row in cur.fetchall()}

    for column_name, column_type in [
        ("densidad_teorica", "REAL"),
        ("volumen_base_l", "REAL"),
    ]:
        if column_name not in formulation_columns:
            cur.execute(
                f"ALTER TABLE formulaciones ADD COLUMN {column_name} {column_type}"
            )

    # Migración de items: densidad, litros y porcentaje m/m.
    cur.execute("PRAGMA table_info(formulacion_items)")
    item_columns = {row[1] for row in cur.fetchall()}

    for column_name, column_type in [
        ("densidad", "REAL"),
        ("cantidad_l", "REAL"),
        ("porcentaje_mm", "REAL"),
    ]:
        if column_name not in item_columns:
            cur.execute(
                f"ALTER TABLE formulacion_items ADD COLUMN {column_name} {column_type}"
            )

    cur.execute(
        """
        UPDATE ordenes
        SET cantidad_real_producida = COALESCE(cantidad_producida, 0)
        WHERE cantidad_real_producida IS NULL
        """
    )

    # Cargar personas iniciales de SEV Tareas.
    for person in SEV_PERSONAS:
        cur.execute(
            """
            INSERT OR IGNORE INTO personas (
                nombre, email, rol, activo, created_at
            )
            VALUES (?, ?, ?, 1, ?)
            """,
            (
                person["nombre"],
                person["email"],
                person["rol"],
                datetime.now().isoformat(timespec="seconds"),
            ),
        )

    # ========================================================
    # Seed de ADJUVANTE G y su formulación maestra.
    # ========================================================

    now_seed = datetime.now().isoformat(timespec="seconds")

    # Asegurar familia ADJ.
    cur.execute(
        """
        INSERT OR IGNORE INTO familias (
            codigo, nombre, activo, created_at
        )
        VALUES ('ADJ', 'Adyuvantes', 1, ?)
        """,
        (now_seed,),
    )

    cur.execute(
        "SELECT id FROM familias WHERE codigo = 'ADJ' LIMIT 1"
    )
    adj_family_row = cur.fetchone()
    adj_family_id = int(adj_family_row[0]) if adj_family_row else None

    # Asegurar producto Adjuvante G.
    if adj_family_id:
        cur.execute(
            """
            SELECT id
            FROM productos
            WHERE LOWER(nombre) = LOWER(?)
              AND familia_id = ?
            LIMIT 1
            """,
            (
                DEFAULT_ADJ_G_FORMULA["producto"],
                adj_family_id,
            ),
        )
        product_row = cur.fetchone()

        if product_row:
            adj_g_product_id = int(product_row[0])
        else:
            cur.execute(
                """
                INSERT INTO productos (
                    nombre,
                    linea,
                    familia_id,
                    unidad,
                    activo,
                    created_at
                )
                VALUES (?, 'ADJ', ?, 'L', 1, ?)
                """,
                (
                    DEFAULT_ADJ_G_FORMULA["producto"],
                    adj_family_id,
                    now_seed,
                ),
            )
            adj_g_product_id = int(cur.lastrowid)

        # Asegurar materias primas de la formulación.
        material_ids = {}

        for item in DEFAULT_ADJ_G_FORMULA["items"]:
            cur.execute(
                """
                SELECT id
                FROM materias_primas
                WHERE UPPER(codigo) = UPPER(?)
                LIMIT 1
                """,
                (item["codigo"],),
            )
            mp_row = cur.fetchone()

            if mp_row:
                material_id = int(mp_row[0])
                cur.execute(
                    """
                    UPDATE materias_primas
                    SET nombre = ?,
                        unidad = 'kg',
                        activo = 1
                    WHERE id = ?
                    """,
                    (
                        item["nombre"],
                        material_id,
                    ),
                )
            else:
                cur.execute(
                    """
                    INSERT INTO materias_primas (
                        codigo,
                        nombre,
                        unidad,
                        activo,
                        created_at
                    )
                    VALUES (?, ?, 'kg', 1, ?)
                    """,
                    (
                        item["codigo"],
                        item["nombre"],
                        now_seed,
                    ),
                )
                material_id = int(cur.lastrowid)

            material_ids[item["codigo"]] = material_id

        # Crear la V1 sólo si el producto no tiene una formulación activa.
        cur.execute(
            """
            SELECT id
            FROM formulaciones
            WHERE producto_id = ?
              AND activa = 1
            ORDER BY version DESC
            LIMIT 1
            """,
            (adj_g_product_id,),
        )
        active_formula_row = cur.fetchone()

        if not active_formula_row:
            cur.execute(
                """
                INSERT INTO formulaciones (
                    producto_id,
                    version,
                    cantidad_base,
                    unidad_base,
                    observaciones,
                    activa,
                    created_at,
                    densidad_teorica,
                    volumen_base_l
                )
                VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?)
                """,
                (
                    adj_g_product_id,
                    int(DEFAULT_ADJ_G_FORMULA["version"]),
                    float(DEFAULT_ADJ_G_FORMULA["cantidad_base"]),
                    DEFAULT_ADJ_G_FORMULA["unidad_base"],
                    DEFAULT_ADJ_G_FORMULA["observaciones"],
                    now_seed,
                    float(DEFAULT_ADJ_G_FORMULA["densidad_teorica"]),
                    float(DEFAULT_ADJ_G_FORMULA["volumen_base_l"]),
                ),
            )
            formula_id = int(cur.lastrowid)

            for item in DEFAULT_ADJ_G_FORMULA["items"]:
                cur.execute(
                    """
                    INSERT INTO formulacion_items (
                        formulacion_id,
                        materia_prima_id,
                        cantidad,
                        unidad,
                        observacion,
                        densidad,
                        cantidad_l,
                        porcentaje_mm
                    )
                    VALUES (?, ?, ?, 'kg', ?, ?, ?, ?)
                    """,
                    (
                        formula_id,
                        material_ids[item["codigo"]],
                        float(item["cantidad_kg"]),
                        "Formulação G Surfactante - STEPAN",
                        float(item["densidad"]),
                        float(item["cantidad_l"]),
                        float(item["porcentaje_mm"]),
                    ),
                )

    # Cargar consumibles y embalajes del listado SIGA.
    for code, description, warehouse, category, balance in DEFAULT_CONSUMABLES:
        cur.execute(
            """
            INSERT OR IGNORE INTO consumibles (
                codigo,
                descripcion,
                armazem,
                categoria,
                saldo_referencia,
                activo,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, 1, ?)
            """,
            (
                code,
                description,
                warehouse,
                category,
                balance,
                datetime.now().isoformat(timespec="seconds"),
            ),
        )

    # Cargar familias iniciales sólo si no existen.
    for code, name in DEFAULT_FAMILIES:
        cur.execute(
            """
            INSERT OR IGNORE INTO familias (
                codigo, nombre, activo, created_at
            )
            VALUES (?, ?, 1, ?)
            """,
            (
                code,
                name,
                datetime.now().isoformat(timespec="seconds"),
            ),
        )

    # Vincular productos existentes por su antiguo código de línea.
    cur.execute(
        """
        UPDATE productos
        SET familia_id = (
            SELECT f.id
            FROM familias f
            WHERE f.codigo = productos.linea
        )
        WHERE familia_id IS NULL
        """
    )

    conn.commit()
    conn.close()


def fetch_df(query, params=()):
    conn = get_conn()
    try:
        return pd.read_sql_query(query, conn, params=params)
    finally:
        conn.close()


def execute(query, params=()):
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(query, params)
        conn.commit()
        lastrowid = cur.lastrowid
    finally:
        conn.close()

    # Backup sólo para operaciones que modifican información.
    normalized = str(query).lstrip().upper()
    if normalized.startswith(("INSERT", "UPDATE", "DELETE", "REPLACE")):
        ok, msg = backup_db_to_github(reason="guardado")
        if not ok and PERSISTENCE_ENABLED:
            st.session_state["last_backup_error"] = msg

    return lastrowid


init_db()


seed_materias_primas_mata225()
# ============================================================
# UTILIDADES DE CÓDIGO
# ============================================================

def next_sequence(table: str, field: str, prefix: str) -> int:
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            f"""
            SELECT {field}
            FROM {table}
            WHERE {field} LIKE ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (f"{prefix}-%",),
        )
        row = cur.fetchone()
        if not row:
            return 1

        code = str(row[0])
        try:
            return int(code.split("-")[-1]) + 1
        except Exception:
            return 1
    finally:
        conn.close()


def generate_lot_code(family_code: str, prod_date: date) -> str:
    # Formato dinámico:
    # SEV-PRD-<FAMILIA>-2026-0001
    family_code = normalize_family_code(family_code)
    prefix = f"SEV-PRD-{family_code}-{prod_date.year}"
    seq = next_sequence("ordenes", "lote_codigo", prefix)
    return f"{prefix}-{seq:04d}"




def fmt_qty(value, unit):
    if pd.isna(value):
        return "—"
    if unit in ("L", "kg"):
        return f"{float(value):,.1f} {unit}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"{float(value):,.0f} {unit}".replace(",", ".")


def add_event(order_id: int, event: str, detail: str = ""):
    execute(
        """
        INSERT INTO eventos (orden_id, fecha, evento, detalle)
        VALUES (?, ?, ?, ?)
        """,
        (
            order_id,
            datetime.now().isoformat(timespec="seconds"),
            event,
            detail,
        ),
    )


def update_total_produced(order_id: int):
    df = fetch_df(
        """
        SELECT COALESCE(SUM(cantidad), 0) AS total
        FROM movimientos_produccion
        WHERE orden_id = ?
        """,
        (order_id,),
    )
    total = float(df.iloc[0]["total"]) if not df.empty else 0.0

    execute(
        """
        UPDATE ordenes
        SET cantidad_producida = ?,
            cantidad_real_producida = ?,
            updated_at = ?
        WHERE id = ?
        """,
        (
            total,
            total,
            datetime.now().isoformat(timespec="seconds"),
            order_id,
        ),
    )



def get_families(active_only=True):
    where = "WHERE activo = 1" if active_only else ""
    return fetch_df(
        f"""
        SELECT id, codigo, nombre, activo, created_at
        FROM familias
        {where}
        ORDER BY codigo, nombre
        """
    )


def get_products(active_only=True):
    where = "WHERE p.activo = 1" if active_only else ""
    return fetch_df(
        f"""
        SELECT
            p.id,
            p.nombre,
            p.unidad,
            p.activo,
            p.created_at,
            p.familia_id,
            f.codigo AS familia_codigo,
            f.nombre AS familia_nombre
        FROM productos p
        LEFT JOIN familias f
            ON f.id = p.familia_id
        {where}
        ORDER BY f.codigo, p.nombre
        """
    )


def normalize_family_code(value: str) -> str:
    value = (value or "").strip().upper()
    value = "".join(ch for ch in value if ch.isalnum())
    return value[:8]


def generate_unique_order_code(order_date: date) -> str:
    """
    Número único de orden interna.
    Ejemplo: SEV-OP-2026-000001
    Nunca se reutiliza dentro de la base.
    """
    prefix = f"SEV-OP-{order_date.year}"
    seq = next_sequence("ordenes", "orden_codigo", prefix)
    return f"{prefix}-{seq:06d}"



def get_people(active_only=True):
    where = "WHERE activo = 1" if active_only else ""
    return fetch_df(
        f"""
        SELECT id, nombre, email, rol, activo, created_at
        FROM personas
        {where}
        ORDER BY
            CASE rol WHEN 'Administrador' THEN 0 ELSE 1 END,
            nombre
        """
    )



def month_end(d: date) -> date:
    last_day = calendar.monthrange(d.year, d.month)[1]
    return date(d.year, d.month, last_day)


def get_order_close_deadline(order_row):
    raw = order_row.get("fecha_orden")
    if not raw:
        return None
    d = pd.to_datetime(raw, errors="coerce")
    if pd.isna(d):
        return None
    return month_end(d.date())


def is_order_close_overdue(order_row, today_value=None):
    today_value = today_value or date.today()
    deadline = get_order_close_deadline(order_row)
    if deadline is None:
        return False
    status = str(order_row.get("estado") or "")
    return status not in ("Finalizada", "Cancelada") and today_value > deadline


def validate_finish_date(order_row, finish_date):
    deadline = get_order_close_deadline(order_row)
    if finish_date is None or deadline is None:
        return True, deadline
    return finish_date <= deadline, deadline


def months_after(start_date: date, months: int) -> date:
    """Suma meses manteniendo el día cuando es posible."""
    months = int(months)
    year = start_date.year + (start_date.month - 1 + months) // 12
    month = (start_date.month - 1 + months) % 12 + 1

    month_lengths = [
        31,
        29 if (
            year % 4 == 0
            and (
                year % 100 != 0
                or year % 400 == 0
            )
        ) else 28,
        31, 30, 31, 30,
        31, 31, 30, 31, 30, 31,
    ]

    day = min(
        start_date.day,
        month_lengths[month - 1],
    )

    return date(year, month, day)


def build_lot_email(order_row, recipients):
    fecha_inicio = order_row.get("fecha_inicio") or "—"
    fecha_fin = order_row.get("fecha_fin") or "Pendiente"
    fecha_vencimiento = order_row.get("fecha_vencimiento") or "Pendiente"

    subject = (
        f"SEV Producción · Nuevo lote {order_row['lote_codigo']} · "
        f"{order_row['producto']}"
    )

    body = f"""Se generó una nueva orden interna de producción.

Orden interna: {order_row['orden_codigo']}
Lote: {order_row['lote_codigo']}
Familia: {order_row['linea']}
Producto: {order_row['producto']}
Cantidad planificada: {order_row['cantidad_planificada']} {order_row['unidad']}
Fecha de inicio: {fecha_inicio}
Fecha de finalización: {fecha_fin}
Fecha de vencimiento: {fecha_vencimiento}
Responsable: {order_row.get('responsable') or '—'}
Estado: {order_row['estado']}
Prioridad: {order_row['prioridad']}

Observaciones:
{order_row.get('observaciones') or '—'}

SEV | Control de Producción
"""

    return subject, body


def try_send_email(recipients, subject, body):
    """
    Envía automáticamente si existen credenciales SMTP en st.secrets.
    Si no existen, devuelve False y la app mantiene el correo generado.
    """
    try:
        smtp_host = st.secrets.get("SMTP_HOST")
        smtp_port = int(st.secrets.get("SMTP_PORT", 587))
        smtp_user = st.secrets.get("SMTP_USER")
        smtp_password = st.secrets.get("SMTP_PASSWORD")
        smtp_from = st.secrets.get("SMTP_FROM", smtp_user)

        if not all(
            [
                smtp_host,
                smtp_user,
                smtp_password,
                smtp_from,
            ]
        ):
            return False, "SMTP no configurado"

        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = smtp_from
        msg["To"] = ", ".join(recipients)
        msg.set_content(body)

        with smtplib.SMTP(
            smtp_host,
            smtp_port,
            timeout=20,
        ) as server:
            server.starttls()
            server.login(
                smtp_user,
                smtp_password,
            )
            server.send_message(msg)

        return True, "Enviado"

    except Exception as exc:
        return False, str(exc)


def save_notification(
    order_id,
    recipients,
    subject,
    body,
    status,
):
    execute(
        """
        INSERT INTO notificaciones (
            orden_id,
            destinatarios,
            asunto,
            cuerpo,
            estado,
            created_at
        )
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            int(order_id),
            "; ".join(recipients),
            subject,
            body,
            status,
            datetime.now().isoformat(
                timespec="seconds"
            ),
        ),
    )


def is_admin():
    return st.session_state.get(
        "current_role"
    ) == "Administrador"




def get_consumables(active_only=True):
    where = "WHERE activo = 1" if active_only else ""
    return fetch_df(
        f"""
        SELECT
            id,
            codigo,
            descripcion,
            armazem,
            categoria,
            saldo_referencia,
            activo,
            created_at
        FROM consumibles
        {where}
        ORDER BY categoria, codigo, armazem
        """
    )


def get_consumable_usage(order_id):
    return fetch_df(
        """
        SELECT
            cc.id,
            cc.fecha,
            c.codigo,
            c.descripcion,
            c.categoria,
            cc.cantidad_real,
            cc.unidad,
            cc.lote_consumible,
            cc.observacion
        FROM consumos_consumibles cc
        JOIN consumibles c
            ON c.id = cc.consumible_id
        WHERE cc.orden_id = ?
        ORDER BY cc.fecha, cc.id
        """,
        (int(order_id),),
    )


def get_raw_materials(active_only=True):
    where = "WHERE activo = 1" if active_only else ""
    return fetch_df(
        f"""
        SELECT
            id,
            codigo,
            codigo_totvs,
            nombre,
            unidad,
            unidad_stock,
            almacen,
            saldo_actual,
            densidad,
            activo,
            created_at
        FROM materias_primas
        {where}
        ORDER BY COALESCE(codigo_totvs, codigo), nombre
        """
    )


def get_active_formulation(product_id):
    df = fetch_df(
        """
        SELECT *
        FROM formulaciones
        WHERE producto_id = ?
          AND activa = 1
        ORDER BY version DESC
        LIMIT 1
        """,
        (int(product_id),),
    )
    return None if df.empty else df.iloc[0].to_dict()


def get_formulation_items(formulation_id):
    if not formulation_id:
        return pd.DataFrame()

    return fetch_df(
        """
        SELECT
            fi.id,
            fi.formulacion_id,
            fi.materia_prima_id,
            mp.codigo AS mp_codigo,
            mp.nombre AS materia_prima,
            fi.cantidad,
            fi.unidad,
            fi.observacion,
            fi.densidad,
            fi.cantidad_l,
            fi.porcentaje_mm
        FROM formulacion_items fi
        JOIN materias_primas mp
          ON mp.id = fi.materia_prima_id
        WHERE fi.formulacion_id = ?
        ORDER BY mp.codigo, mp.nombre
        """,
        (int(formulation_id),),
    )


def calculate_theoretical_consumption(order_row):
    """
    Calcula el consumo teórico de materias primas respetando la base
    gravimétrica de la receta.

    Para formulaciones expresadas en kg:
    - Si la orden está en kg, escala directamente.
    - Si la orden está en L, convierte el volumen solicitado a kg
      usando la densidad teórica del producto.
    """
    formulation_id = order_row.get("formulacion_id")

    if formulation_id is None or pd.isna(formulation_id):
        return pd.DataFrame()

    formulation = fetch_df(
        "SELECT * FROM formulaciones WHERE id = ?",
        (int(formulation_id),),
    )

    if formulation.empty:
        return pd.DataFrame()

    formulation = formulation.iloc[0]
    items = get_formulation_items(int(formulation_id))

    if items.empty:
        return pd.DataFrame()

    requested_qty = float(
        order_row.get("cantidad_planificada") or 0
    )
    requested_unit = str(
        order_row.get("unidad") or ""
    ).strip()

    base_qty = float(
        formulation["cantidad_base"] or 1
    )
    base_unit = str(
        formulation["unidad_base"] or ""
    ).strip()

    density = pd.to_numeric(
        pd.Series([formulation.get("densidad_teorica")]),
        errors="coerce",
    ).iloc[0]

    # Convertir cantidad solicitada a la unidad base de la fórmula.
    if requested_unit == base_unit:
        equivalent_base_qty = requested_qty
    elif (
        requested_unit == "L"
        and base_unit == "kg"
        and pd.notna(density)
        and float(density) > 0
    ):
        equivalent_base_qty = requested_qty * float(density)
    elif (
        requested_unit == "kg"
        and base_unit == "L"
        and pd.notna(density)
        and float(density) > 0
    ):
        equivalent_base_qty = requested_qty / float(density)
    else:
        equivalent_base_qty = requested_qty

    factor = (
        equivalent_base_qty / base_qty
        if base_qty > 0
        else 0
    )

    out = items.copy()

    out["consumo_teorico"] = (
        pd.to_numeric(
            out["cantidad"],
            errors="coerce",
        ).fillna(0)
        * factor
    )

    out["consumo_teorico_l"] = (
        pd.to_numeric(
            out["cantidad_l"],
            errors="coerce",
        ).fillna(0)
        * factor
    )

    out["factor_escala"] = factor
    out["masa_objetivo_kg"] = (
        equivalent_base_qty
        if base_unit == "kg"
        else np.nan
    )

    return out




def save_order_admin_changes(order_id, values):
    """
    Guarda todos los campos editables de una orden en una sola transacción,
    verifica rowcount y vuelve a leer el registro para confirmar persistencia.
    """
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            UPDATE ordenes
            SET linea = ?,
                producto = ?,
                producto_id = ?,
                formulacion_id = ?,
                cantidad_planificada = ?,
                cantidad_real_producida = ?,
                cantidad_producida = ?,
                unidad = ?,
                fecha_orden = ?,
                fecha_inicio = ?,
                fecha_fin = ?,
                fecha_vencimiento = ?,
                vida_util_meses = ?,
                responsable = ?,
                responsable_email = ?,
                estado = ?,
                prioridad = ?,
                observaciones = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (
                values["linea"],
                values["producto"],
                int(values["producto_id"]),
                (
                    int(values["formulacion_id"])
                    if values["formulacion_id"] is not None
                    else None
                ),
                float(values["cantidad_planificada"]),
                float(values["cantidad_real_producida"]),
                float(values["cantidad_real_producida"]),
                values["unidad"],
                values["fecha_orden"],
                values["fecha_inicio"],
                values["fecha_fin"],
                values["fecha_vencimiento"],
                int(values["vida_util_meses"]),
                values["responsable"],
                values["responsable_email"],
                values["estado"],
                values["prioridad"],
                values["observaciones"],
                datetime.now().isoformat(timespec="seconds"),
                int(order_id),
            ),
        )

        if cur.rowcount != 1:
            conn.rollback()
            return False, None, f"No se actualizó la orden. Filas modificadas: {cur.rowcount}"

        conn.commit()
        backup_db_to_github(reason="modificación de orden")

        row = conn.execute(
            "SELECT * FROM ordenes WHERE id = ?",
            (int(order_id),),
        ).fetchone()

        saved = dict(row) if row else None

        if not saved:
            return False, None, "La orden no pudo volver a leerse después del guardado."

        return True, saved, "Cambios guardados correctamente."

    except Exception as exc:
        conn.rollback()
        return False, None, str(exc)
    finally:
        conn.close()


def get_order_by_id(order_id):
    df = fetch_df(
        "SELECT * FROM ordenes WHERE id = ?",
        (int(order_id),),
    )
    return None if df.empty else df.iloc[0].to_dict()


def sync_order_material_reservation(order_id):
    """
    Sincroniza el descuento teórico de materias primas para una orden.

    - Si se asigna una fórmula, descuenta el consumo teórico del stock.
    - Si cambia la cantidad o la fórmula, descuenta/devuelve sólo la diferencia.
    - Si se vuelve a guardar sin cambios, NO vuelve a descontar.
    - Si se quita la fórmula, devuelve la reserva anterior al stock.
    """
    order = get_order_by_id(order_id)
    if not order:
        return False, "Orden no encontrada."

    theoretical = calculate_theoretical_consumption(order)

    desired = {}
    if not theoretical.empty:
        for _, r in theoretical.iterrows():
            desired[int(r["materia_prima_id"])] = {
                "qty": float(r["consumo_teorico"] or 0),
                "unit": str(r.get("unidad") or "kg"),
            }

    conn = get_conn()
    try:
        cur = conn.cursor()

        current_rows = cur.execute(
            """
            SELECT materia_prima_id, cantidad_teorica, unidad
            FROM reservas_materias_primas
            WHERE orden_id = ?
            """,
            (int(order_id),),
        ).fetchall()

        current = {
            int(r["materia_prima_id"]): {
                "qty": float(r["cantidad_teorica"] or 0),
                "unit": str(r["unidad"] or "kg"),
            }
            for r in current_rows
        }

        all_ids = set(current) | set(desired)
        now = datetime.now().isoformat(timespec="seconds")

        for mp_id in all_ids:
            old_qty = current.get(mp_id, {}).get("qty", 0.0)
            new_qty = desired.get(mp_id, {}).get("qty", 0.0)
            delta = new_qty - old_qty

            if abs(delta) > 1e-9:
                # delta positivo = consumir/descontar más
                # delta negativo = devolver stock
                cur.execute(
                    """
                    UPDATE materias_primas
                    SET saldo_actual = COALESCE(saldo_actual, 0) - ?
                    WHERE id = ?
                    """,
                    (float(delta), int(mp_id)),
                )

            if mp_id in desired:
                cur.execute(
                    """
                    INSERT INTO reservas_materias_primas (
                        orden_id,
                        materia_prima_id,
                        cantidad_teorica,
                        unidad,
                        updated_at
                    )
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(orden_id, materia_prima_id)
                    DO UPDATE SET
                        cantidad_teorica = excluded.cantidad_teorica,
                        unidad = excluded.unidad,
                        updated_at = excluded.updated_at
                    """,
                    (
                        int(order_id),
                        int(mp_id),
                        float(new_qty),
                        desired[mp_id]["unit"],
                        now,
                    ),
                )
            else:
                cur.execute(
                    """
                    DELETE FROM reservas_materias_primas
                    WHERE orden_id = ?
                      AND materia_prima_id = ?
                    """,
                    (int(order_id), int(mp_id)),
                )

        conn.commit()
        backup_db_to_github(reason="sincronización de stock")
        return True, "Stock teórico sincronizado."
    except Exception as exc:
        conn.rollback()
        return False, str(exc)
    finally:
        conn.close()


def get_order_reserved_materials(order_id):
    return fetch_df(
        """
        SELECT
            r.materia_prima_id,
            COALESCE(mp.codigo_totvs, mp.codigo) AS codigo_totvs,
            mp.nombre AS materia_prima,
            r.cantidad_teorica,
            r.unidad,
            mp.saldo_actual
        FROM reservas_materias_primas r
        JOIN materias_primas mp
          ON mp.id = r.materia_prima_id
        WHERE r.orden_id = ?
        ORDER BY COALESCE(mp.codigo_totvs, mp.codigo), mp.nombre
        """,
        (int(order_id),),
    )


def get_actual_consumption(order_id):
    return fetch_df(
        """
        SELECT
            cmp.materia_prima_id,
            mp.codigo AS mp_codigo,
            mp.nombre AS materia_prima,
            cmp.unidad,
            SUM(cmp.cantidad_real) AS consumo_real
        FROM consumos_materias_primas cmp
        JOIN materias_primas mp
          ON mp.id = cmp.materia_prima_id
        WHERE cmp.orden_id = ?
        GROUP BY
            cmp.materia_prima_id,
            mp.codigo,
            mp.nombre,
            cmp.unidad
        ORDER BY mp.codigo, mp.nombre
        """,
        (int(order_id),),
    )


def production_variance(theoretical, actual):
    theoretical = float(theoretical or 0)
    actual = float(actual or 0)
    if theoretical <= 0:
        return 0.0
    return (actual - theoretical) / theoretical * 100.0



def lot_status_label(row):
    if is_order_close_overdue(row):
        return "ERROR CIERRE"

    status = str(row.get("estado") or "")
    if status == "Finalizada":
        return "CERRADA"
    if status == "Cancelada":
        return "CANCELADA"
    if status == "En producción":
        return "EN PRODUCCIÓN"
    if status == "En preparación":
        return "EN PREPARACIÓN"
    return "ABIERTA"


def lot_status_color(status):
    return {
        "ERROR CIERRE": "#b91c1c",
        "CERRADA": "#166534",
        "CANCELADA": "#991b1b",
        "EN PRODUCCIÓN": "#1d4ed8",
        "EN PREPARACIÓN": "#b45309",
        "ABIERTA": "#0f766e",
    }.get(status, "#475569")


def get_orders():
    return fetch_df(
        """
        SELECT *
        FROM ordenes
        ORDER BY
            CASE estado
                WHEN 'En producción' THEN 1
                WHEN 'En preparación' THEN 2
                WHEN 'Planificada' THEN 3
                WHEN 'Finalizada' THEN 4
                ELSE 5
            END,
            fecha_orden DESC,
            id DESC
        """
    )


# ============================================================
# CABECERA
# ============================================================

st.markdown(
    f"""
    <div class="sev-title">
        <h1>🏭 SEV | Control de Producción</h1>
        <p>{APP_VERSION} · Órdenes internas · Lotes · Producción · Trazabilidad · Backup persistente</p>
    </div>
    """,
    unsafe_allow_html=True,
)


# ============================================================
# SIDEBAR
# ============================================================

with st.sidebar:
    st.header("Producción SEV")

    people_login = get_people(
        active_only=True
    )

    if not people_login.empty:
        selected_person_id = st.selectbox(
            "Usuario",
            options=people_login["id"].astype(int).tolist(),
            format_func=lambda pid: (
                people_login.loc[
                    people_login["id"] == pid,
                    "nombre",
                ].iloc[0]
                + " · "
                + people_login.loc[
                    people_login["id"] == pid,
                    "rol",
                ].iloc[0]
            ),
        )

        current_person = people_login[
            people_login["id"] == selected_person_id
        ].iloc[0]

        st.session_state["current_user"] = str(
            current_person["nombre"]
        )
        st.session_state["current_email"] = str(
            current_person["email"]
        )
        st.session_state["current_role"] = str(
            current_person["rol"]
        )

        st.caption(
            f"{current_person['email']} · {current_person['rol']}"
        )

    module_options = [
        "Tablero",
        "Nueva orden",
        "Registrar producción",
        "Órdenes y lotes",
        "Familias",
        "Productos",
        "Materias primas",
        "Consumibles",
        "Formulaciones",
        "Personas y correos",
        "Backup / recuperación",
    ]

    default_module_index = 3 if st.session_state.pop("go_orders", False) else 0

    section = st.radio(
        "Módulo",
        module_options,
        index=default_module_index,
    )

    st.divider()

    st.caption("Formato de lotes")
    st.code("SEV-PRD-<FAMILIA>-2026-0001")

    st.caption(
        "Cada familia tiene su propia numeración anual de lotes."
    )


# ============================================================
# TABLERO
# ============================================================

if section == "Tablero":

    orders = get_orders()

    if orders.empty:
        total_orders = active_orders = finished_orders = 0
        total_qty = 0
    else:
        total_orders = len(orders)
        active_orders = int(
            orders["estado"].isin(
                ["Planificada", "En preparación", "En producción"]
            ).sum()
        )
        finished_orders = int((orders["estado"] == "Finalizada").sum())
        total_qty = float(
            pd.to_numeric(
                orders["cantidad_producida"],
                errors="coerce",
            ).fillna(0).sum()
        )

    today_ts = pd.Timestamp(date.today())

    if not orders.empty and "fecha_vencimiento" in orders.columns:
        expiry_dates = pd.to_datetime(
            orders["fecha_vencimiento"],
            errors="coerce",
        )

        expiring_90 = int(
            (
                expiry_dates.notna()
                & (expiry_dates >= today_ts)
                & (
                    expiry_dates
                    <= today_ts + pd.Timedelta(days=90)
                )
            ).sum()
        )

        expired = int(
            (
                expiry_dates.notna()
                & (expiry_dates < today_ts)
            ).sum()
        )
    else:
        expiring_90 = 0
        expired = 0

    close_errors = (
        int(orders.apply(lambda r: is_order_close_overdue(r), axis=1).sum())
        if not orders.empty
        else 0
    )

    c1, c2, c3, c4, c5, c6 = st.columns(6)

    c1.metric("Órdenes totales", total_orders)
    c2.metric("Órdenes activas", active_orders)
    c3.metric("Finalizadas", finished_orders)
    c4.metric("Cantidad producida", f"{total_qty:,.1f}".replace(",", "."))
    c5.metric("Vencidos", expired)
    c6.metric("Errores de cierre", close_errors)

    if close_errors > 0:
        st.error(
            f"⚠️ Hay {close_errors} orden(es) abiertas fuera del mes de creación. "
            "Se consideran ERROR DE CIERRE."
        )

    st.subheader("Producción en curso")

    if orders.empty:
        st.info("Todavía no existen órdenes de producción.")
    else:
        active = orders[
            orders["estado"].isin(
                ["Planificada", "En preparación", "En producción"]
            )
        ].copy()

        if active.empty:
            st.success("No hay órdenes activas.")
        else:
            for _, row in active.head(12).iterrows():
                planned = float(row["cantidad_planificada"] or 0)
                produced = float(row["cantidad_real_producida"] or 0)
                progress = (produced / planned * 100) if planned > 0 else 0

                status_label = lot_status_label(row)
                status_color = lot_status_color(status_label)

                st.markdown(
                    f"""
                    <div class="sev-box">
                        <div class="sev-lot">{row['lote_codigo']}
                        <span class="lot-status" style="background:{status_color};">{status_label}</span>
                        </div>
                        <b>{row['producto']}</b> · {row['linea']} · {row['estado']}<br>
                        Planificado: {fmt_qty(planned, row['unidad'])} ·
                        Producido: {fmt_qty(produced, row['unidad'])} ·
                        Avance: {progress:.1f}%<br>
                        <span class="small-note">
                            Orden: {row['orden_codigo']} · Responsable: {row['responsable'] or '—'}
                        </span>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )

                if is_admin():
                    if st.button(
                        f"✏️ Modificar {row['lote_codigo']}",
                        key=f"dashboard_edit_{int(row['id'])}",
                        use_container_width=True,
                    ):
                        st.session_state["edit_order_id"] = int(row["id"])
                        st.session_state["go_orders"] = True
                        st.rerun()

    st.subheader("Tablero de lotes")

    if not orders.empty:
        board = orders[
            [
                "lote_codigo",
                "orden_codigo",
                "producto",
                "linea",
                "cantidad_planificada",
                "cantidad_real_producida",
                "unidad",
                "fecha_inicio",
                "fecha_fin",
                "fecha_vencimiento",
                "responsable",
                "estado",
            ]
        ].copy()

        board.columns = [
            "Lote",
            "Orden",
            "Producto",
            "Familia",
            "Teórico",
            "Real",
            "Unidad",
            "Inicio",
            "Finalización",
            "Vencimiento",
            "Responsable",
            "Estado",
        ]

        board["Fecha límite cierre"] = orders.apply(
            lambda r: (
                get_order_close_deadline(r).isoformat()
                if get_order_close_deadline(r)
                else None
            ),
            axis=1,
        )

        board["Situación"] = orders.apply(
            lot_status_label,
            axis=1,
        )

        def _status_style(value):
            styles = {
                "ERROR CIERRE": "background-color:#fee2e2;color:#b91c1c;font-weight:800;",
                "CERRADA": "background-color:#dcfce7;color:#166534;font-weight:700;",
                "CANCELADA": "background-color:#fee2e2;color:#991b1b;font-weight:700;",
                "EN PRODUCCIÓN": "background-color:#dbeafe;color:#1d4ed8;font-weight:700;",
                "EN PREPARACIÓN": "background-color:#fef3c7;color:#92400e;font-weight:700;",
                "ABIERTA": "background-color:#ccfbf1;color:#115e59;font-weight:700;",
            }
            return styles.get(value, "")

        st.dataframe(
            board.style.map(
                _status_style,
                subset=["Situación"],
            ),
            use_container_width=True,
            hide_index=True,
            height=min(420, 38 + 35 * max(len(board), 1)),
            column_config={
                "Lote": st.column_config.TextColumn(width="medium"),
                "Orden": st.column_config.TextColumn(width="medium"),
                "Producto": st.column_config.TextColumn(width="medium"),
                "Familia": st.column_config.TextColumn(width="small"),
                "Teórico": st.column_config.NumberColumn(format="%.2f"),
                "Real": st.column_config.NumberColumn(format="%.2f"),
                "Unidad": st.column_config.TextColumn(width="small"),
                "Inicio": st.column_config.TextColumn(width="small"),
                "Finalización": st.column_config.TextColumn(width="small"),
                "Vencimiento": st.column_config.TextColumn(width="small"),
                "Responsable": st.column_config.TextColumn(width="medium"),
                "Estado": st.column_config.TextColumn(width="small"),
                "Fecha límite cierre": st.column_config.TextColumn(width="small"),
                "Situación": st.column_config.TextColumn(width="small"),
            },
        )

    st.subheader("Consumo de materias primas por lote")

    st.caption(
        "Muestra solamente el consumo calculado por la formulación de cada lote. "
        "Si se registró lote de materia prima, también se visualiza."
    )

    if orders.empty:
        st.info("No hay lotes para mostrar.")
    else:
        consumption_rows = []

        for _, order_row in orders.iterrows():
            theo = calculate_theoretical_consumption(order_row)

            if theo.empty:
                continue

            actual_lots = fetch_df(
                """
                SELECT
                    cmp.materia_prima_id,
                    GROUP_CONCAT(
                        DISTINCT CASE
                            WHEN TRIM(COALESCE(cmp.lote_mp, '')) <> ''
                            THEN cmp.lote_mp
                        END
                    ) AS lotes_mp
                FROM consumos_materias_primas cmp
                WHERE cmp.orden_id = ?
                GROUP BY cmp.materia_prima_id
                """,
                (int(order_row["id"]),),
            )

            lot_map = {}
            if not actual_lots.empty:
                lot_map = {
                    int(r["materia_prima_id"]): (r["lotes_mp"] or "—")
                    for _, r in actual_lots.iterrows()
                }

            for _, mp in theo.iterrows():
                consumption_rows.append(
                    {
                        "Lote producción": order_row["lote_codigo"],
                        "Producto": order_row["producto"],
                        "Código TOTVS": mp.get("mp_codigo") or "—",
                        "Materia prima": mp.get("materia_prima") or "—",
                        "Consumo teórico kg": float(mp.get("consumo_teorico") or 0),
                        "Consumo teórico L": float(mp.get("consumo_teorico_l") or 0),
                        "Lote materia prima": lot_map.get(
                            int(mp["materia_prima_id"]),
                            "—",
                        ),
                    }
                )

        if consumption_rows:
            consumption_board = pd.DataFrame(consumption_rows)

            st.dataframe(
                consumption_board,
                use_container_width=True,
                hide_index=True,
                height=min(520, 38 + 35 * max(len(consumption_board), 1)),
                column_config={
                    "Lote producción": st.column_config.TextColumn(width="medium"),
                    "Producto": st.column_config.TextColumn(width="medium"),
                    "Código TOTVS": st.column_config.TextColumn(width="small"),
                    "Materia prima": st.column_config.TextColumn(width="large"),
                    "Consumo teórico kg": st.column_config.NumberColumn(format="%.3f"),
                    "Consumo teórico L": st.column_config.NumberColumn(format="%.3f"),
                    "Lote materia prima": st.column_config.TextColumn(width="medium"),
                },
            )
        else:
            st.info(
                "Todavía no hay lotes con formulación asignada para calcular consumos."
            )


    st.subheader("Totalizador de materias primas")

    st.caption(
        "Suma el consumo teórico de cada materia prima considerando todos los lotes "
        "mostrados en el tablero."
    )

    if 'consumption_board' in locals() and not consumption_board.empty:

        totals_mp = (
            consumption_board
            .groupby(
                [
                    "Código TOTVS",
                    "Materia prima",
                ],
                as_index=False,
            )
            .agg(
                {
                    "Consumo teórico kg": "sum",
                    "Consumo teórico L": "sum",
                }
            )
        )

        totals_mp["Cantidad de lotes"] = (
            consumption_board
            .groupby(
                [
                    "Código TOTVS",
                    "Materia prima",
                ]
            )["Lote producción"]
            .nunique()
            .values
        )

        totals_mp = totals_mp[
            [
                "Código TOTVS",
                "Materia prima",
                "Cantidad de lotes",
                "Consumo teórico kg",
                "Consumo teórico L",
            ]
        ].sort_values(
            by="Consumo teórico kg",
            ascending=False,
        )

        # Fila TOTAL GENERAL
        total_general = pd.DataFrame(
            [
                {
                    "Código TOTVS": "TOTAL",
                    "Materia prima": "TOTAL GENERAL",
                    "Cantidad de lotes": int(
                        consumption_board["Lote producción"].nunique()
                    ),
                    "Consumo teórico kg": float(
                        totals_mp["Consumo teórico kg"].sum()
                    ),
                    "Consumo teórico L": float(
                        totals_mp["Consumo teórico L"].sum()
                    ),
                }
            ]
        )

        totals_with_general = pd.concat(
            [
                totals_mp,
                total_general,
            ],
            ignore_index=True,
        )

        st.dataframe(
            totals_with_general.style.format(
                {
                    "Cantidad de lotes": "{:.0f}",
                    "Consumo teórico kg": "{:,.3f}",
                    "Consumo teórico L": "{:,.3f}",
                }
            ),
            use_container_width=True,
            hide_index=True,
            height=min(
                420,
                38 + 35 * max(len(totals_with_general), 1),
            ),
            column_config={
                "Código TOTVS": st.column_config.TextColumn(
                    width="small"
                ),
                "Materia prima": st.column_config.TextColumn(
                    width="large"
                ),
                "Cantidad de lotes": st.column_config.NumberColumn(
                    width="small",
                    format="%d",
                ),
                "Consumo teórico kg": st.column_config.NumberColumn(
                    width="medium",
                    format="%.3f",
                ),
                "Consumo teórico L": st.column_config.NumberColumn(
                    width="medium",
                    format="%.3f",
                ),
            },
        )

        # Indicadores rápidos
        t1, t2, t3 = st.columns(3)

        t1.metric(
            "Materias primas",
            int(len(totals_mp)),
        )

        t2.metric(
            "Total teórico kg",
            f"{totals_mp['Consumo teórico kg'].sum():,.3f}",
        )

        t3.metric(
            "Total teórico L",
            f"{totals_mp['Consumo teórico L'].sum():,.3f}",
        )

        st.download_button(
            "⬇️ Descargar totalizador de materias primas",
            data=totals_with_general.to_csv(
                index=False,
                sep=";",
            ).encode("utf-8-sig"),
            file_name="sev_totalizador_materias_primas.csv",
            mime="text/csv",
            use_container_width=True,
        )

    else:
        st.info(
            "Todavía no hay consumos de materias primas para totalizar."
        )


    st.subheader("Resumen por línea")

    if not orders.empty:
        summary = (
            orders.groupby("linea", as_index=False)
            .agg(
                Ordenes=("id", "count"),
                Planificado=("cantidad_planificada", "sum"),
                Producido=("cantidad_producida", "sum"),
            )
        )

        family_map_df = get_families(active_only=False)
        family_map = {
            str(r["codigo"]): str(r["nombre"])
            for _, r in family_map_df.iterrows()
        }

        summary["Familia"] = summary["linea"].map(
            lambda x: f"{x} · {family_map.get(x, x)}"
        )

        summary["Cumplimiento %"] = (
            summary["Producido"] / summary["Planificado"].replace(0, pd.NA) * 100
        )

        st.dataframe(
            summary[
                ["Familia", "Ordenes", "Planificado", "Producido", "Cumplimiento %"]
            ].style.format(
                {
                    "Planificado": "{:,.1f}",
                    "Producido": "{:,.1f}",
                    "Cumplimiento %": "{:.1f}%",
                },
                na_rep="—",
            ),
            use_container_width=True,
            hide_index=True,
        )


# ============================================================
# NUEVA ORDEN
# ============================================================

elif section == "Nueva orden":

    st.subheader("Nueva orden interna de producción")

    families = get_families(active_only=True)
    products = get_products(active_only=True)

    if families.empty:
        st.warning(
            "No existen familias activas. Primero cargá una familia en el módulo Familias."
        )
    else:
        family_ids = families["id"].astype(int).tolist()

        with st.form("new_order_form", clear_on_submit=False):

            c1, c2 = st.columns(2)

            with c1:
                family_id = st.selectbox(
                    "Familia",
                    options=family_ids,
                    format_func=lambda fid: (
                        f"{families.loc[families['id'] == fid, 'codigo'].iloc[0]} · "
                        f"{families.loc[families['id'] == fid, 'nombre'].iloc[0]}"
                    ),
                )

                selected_family = families[
                    families["id"] == family_id
                ].iloc[0]

                family_code = str(
                    selected_family["codigo"]
                )

                family_products = products[
                    products["familia_id"] == family_id
                ].copy() if not products.empty else pd.DataFrame()

                if family_products.empty:
                    st.info(
                        "Esta familia todavía no tiene productos cargados."
                    )
                    product_id = None
                    producto = ""
                    unidad_default = "L"
                    active_formula = None
                else:
                    product_ids = family_products["id"].astype(int).tolist()
                    product_id = st.selectbox(
                        "Producto",
                        options=product_ids,
                        format_func=lambda pid: (
                            family_products.loc[
                                family_products["id"] == pid,
                                "nombre",
                            ].iloc[0]
                        ),
                    )

                    selected_product = family_products[
                        family_products["id"] == product_id
                    ].iloc[0]

                    producto = str(
                        selected_product["nombre"]
                    )

                    unidad_default = str(
                        selected_product["unidad"]
                    )

                    active_formula = get_active_formulation(
                        int(product_id)
                    )

                    if active_formula:
                        formula_density = active_formula.get(
                            "densidad_teorica"
                        )

                        density_txt = (
                            f" · densidad {float(formula_density):.3f} kg/L"
                            if formula_density is not None
                            and pd.notna(formula_density)
                            else ""
                        )

                        st.success(
                            f"Formulación activa V{active_formula['version']} · "
                            f"base {active_formula['cantidad_base']} {active_formula['unidad_base']}"
                            f"{density_txt}"
                        )
                    else:
                        st.warning(
                            "Este producto todavía no tiene una formulación activa."
                        )

                unidad = st.selectbox(
                    "Unidad",
                    UNIDADES,
                    index=UNIDADES.index(unidad_default)
                    if unidad_default in UNIDADES
                    else 0,
                )

                cantidad = st.number_input(
                    "Cantidad teórica a producir",
                    min_value=0.01,
                    value=250.0,
                    step=10.0,
                )

                if active_formula:
                    density_value = active_formula.get(
                        "densidad_teorica"
                    )

                    if (
                        unidad == "L"
                        and active_formula.get("unidad_base") == "kg"
                        and density_value is not None
                        and pd.notna(density_value)
                        and float(density_value) > 0
                    ):
                        target_mass = float(cantidad) * float(density_value)

                        st.caption(
                            f"Equivalente gravimétrico: "
                            f"{float(cantidad):,.2f} L × "
                            f"{float(density_value):.3f} kg/L = "
                            f"{target_mass:,.2f} kg"
                        )

            with c2:
                fecha_orden = st.date_input(
                    "Fecha de orden",
                    value=date.today(),
                )

                fecha_inicio = st.date_input(
                    "Fecha prevista de inicio",
                    value=date.today(),
                )

                people = get_people(
                    active_only=True
                )

                person_ids = people["id"].astype(int).tolist()

                responsable_id = st.selectbox(
                    "Responsable",
                    options=person_ids,
                    format_func=lambda pid: (
                        people.loc[
                            people["id"] == pid,
                            "nombre",
                        ].iloc[0]
                    ),
                )

                selected_responsable = people[
                    people["id"] == responsable_id
                ].iloc[0]

                responsable = str(
                    selected_responsable["nombre"]
                )

                responsable_email = str(
                    selected_responsable["email"]
                )

                prioridad = st.selectbox(
                    "Prioridad",
                    PRIORIDADES,
                    index=0,
                )

                vida_util_meses = st.number_input(
                    "Vida útil desde finalización [meses]",
                    min_value=1,
                    max_value=120,
                    value=DEFAULT_SHELF_LIFE_MONTHS,
                    step=1,
                )

            email_options = {
                int(r["id"]):
                    f"{r['nombre']} · {r['email']}"
                for _, r in people.iterrows()
            }

            email_recipients_ids = st.multiselect(
                "Enviar aviso de creación de lote a",
                options=list(email_options.keys()),
                default=list(email_options.keys()),
                format_func=lambda pid: email_options[pid],
            )

            observaciones = st.text_area(
                "Observaciones / instrucciones internas",
                height=120,
            )

            lot_preview = generate_lot_code(
                family_code,
                fecha_inicio,
            )

            order_preview = generate_unique_order_code(
                fecha_orden,
            )

            close_deadline_preview = month_end(fecha_orden)

            st.info(
                f"Orden propuesta: **{order_preview}**  ·  "
                f"Lote propuesto: **{lot_preview}**  ·  "
                f"Cierre obligatorio máximo: "
                f"**{close_deadline_preview.strftime('%d/%m/%Y')}**"
            )

            submitted = st.form_submit_button(
                "Crear orden y lote",
                type="primary",
                use_container_width=True,
                disabled=(product_id is None or active_formula is None),
            )

        if submitted:

            now = datetime.now().isoformat(timespec="seconds")

            # Generar de nuevo al momento de guardar para asegurar unicidad.
            order_code = generate_unique_order_code(fecha_orden)
            lot_code = generate_lot_code(family_code, fecha_inicio)

            order_id = execute(
                """
                INSERT INTO ordenes (
                    orden_codigo,
                    lote_codigo,
                    linea,
                    producto,
                    cantidad_planificada,
                    cantidad_producida,
                    unidad,
                    fecha_orden,
                    fecha_inicio,
                    fecha_fin,
                    responsable,
                    responsable_email,
                    prioridad,
                    estado,
                    observaciones,
                    vida_util_meses,
                    fecha_vencimiento,
                    producto_id,
                    formulacion_id,
                    cantidad_real_producida,
                    created_at,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, 0, ?, ?, ?, NULL, ?, ?, ?, 'Planificada', ?, ?, NULL, ?, ?, 0, ?, ?)
                """,
                (
                    order_code,
                    lot_code,
                    family_code,
                    producto,
                    float(cantidad),
                    unidad,
                    fecha_orden.isoformat(),
                    fecha_inicio.isoformat(),
                    responsable.strip(),
                    responsable_email.strip(),
                    prioridad,
                    observaciones.strip(),
                    int(vida_util_meses),
                    int(product_id),
                    int(active_formula["id"]),
                    now,
                    now,
                ),
            )

            add_event(
                order_id,
                "Orden creada",
                f"{order_code} · {lot_code} · {producto}",
            )

            stock_ok, stock_msg = sync_order_material_reservation(
                int(order_id)
            )

            if stock_ok:
                add_event(
                    int(order_id),
                    "Stock teórico reservado",
                    "Descuento automático según formulación activa y cantidad teórica.",
                )
            else:
                st.warning(
                    f"La orden fue creada, pero no se pudo sincronizar el stock: {stock_msg}"
                )

            created_order = fetch_df(
                """
                SELECT *
                FROM ordenes
                WHERE id = ?
                """,
                (int(order_id),),
            ).iloc[0].to_dict()

            recipients = []
            if email_recipients_ids:
                recipients = people[
                    people["id"].isin(
                        email_recipients_ids
                    )
                ]["email"].astype(str).tolist()

            if recipients:
                subject, body = build_lot_email(
                    created_order,
                    recipients,
                )

                sent, send_status = try_send_email(
                    recipients,
                    subject,
                    body,
                )

                save_notification(
                    order_id,
                    recipients,
                    subject,
                    body,
                    "Enviado" if sent else "Generado",
                )

                st.session_state[
                    "last_generated_email"
                ] = {
                    "recipients": recipients,
                    "subject": subject,
                    "body": body,
                    "sent": sent,
                    "status": send_status,
                }

            st.success(
                f"Orden creada correctamente. "
                f"Orden: {order_code} · Lote: {lot_code}"
            )

            email_info = st.session_state.get(
                "last_generated_email"
            )

            if email_info:
                if email_info["sent"]:
                    st.success(
                        "Correo de creación de lote enviado automáticamente."
                    )
                else:
                    st.info(
                        "El correo quedó generado. "
                        "Para envío automático configurá SMTP en Streamlit Secrets."
                    )

                    mailto = (
                        "mailto:"
                        + ",".join(email_info["recipients"])
                        + "?subject="
                        + quote(email_info["subject"])
                        + "&body="
                        + quote(email_info["body"])
                    )

                    st.link_button(
                        "✉️ Abrir correo generado",
                        mailto,
                    )

                    st.text_area(
                        "Vista previa del correo",
                        value=email_info["body"],
                        height=280,
                    )


# ============================================================
# REGISTRAR PRODUCCIÓN
# ============================================================

elif section == "Registrar producción":

    st.subheader("Registrar cantidad producida")

    orders = get_orders()

    active = orders[
        orders["estado"].isin(
            ["Planificada", "En preparación", "En producción"]
        )
    ].copy() if not orders.empty else pd.DataFrame()

    if active.empty:
        st.info("No hay órdenes activas para registrar producción.")

    else:
        labels = {
            int(row["id"]):
                f"{row['lote_codigo']} · {row['producto']} · {row['estado']}"
            for _, row in active.iterrows()
        }

        selected_id = st.selectbox(
            "Orden / Lote",
            options=list(labels.keys()),
            format_func=lambda x: labels[x],
        )

        # Leer siempre el dato actualizado directamente desde SQLite.
        row_dict = get_order_by_id(int(selected_id))
        row = pd.Series(row_dict) if row_dict else active[
            active["id"] == selected_id
        ].iloc[0]

        planned = float(row.get("cantidad_planificada") or 0)

        produced = float(
            row.get("cantidad_real_producida")
            if pd.notna(row.get("cantidad_real_producida"))
            else row.get("cantidad_producida")
            or 0
        )

        remaining = max(planned - produced, 0)
        variance = production_variance(planned, produced)

        c1, c2, c3, c4 = st.columns(4)

        c1.metric(
            "Producción teórica",
            fmt_qty(planned, row["unidad"]),
        )

        c2.metric(
            "Producción real",
            fmt_qty(produced, row["unidad"]),
        )

        c3.metric(
            "Pendiente",
            fmt_qty(remaining, row["unidad"]),
        )

        c4.metric(
            "Ajuste",
            f"{variance:+.1f}%",
        )

        st.markdown(
            f"""
            <div class="sev-box">
                <div class="sev-lot">{row['lote_codigo']}</div>
                Orden interna: <b>{row['orden_codigo']}</b><br>
                Producto: <b>{row['producto']}</b><br>
                Responsable: {row['responsable'] or '—'}<br>
                Fecha máxima de cierre:
                <b>{
                    get_order_close_deadline(row).strftime('%d/%m/%Y')
                    if get_order_close_deadline(row)
                    else '—'
                }</b>
            </div>
            """,
            unsafe_allow_html=True,
        )

        # ====================================================
        # CARGA DE PRODUCCIÓN - ARRIBA Y VISIBLE
        # ====================================================

        st.subheader("Carga de producción real")

        st.caption(
            "Podés ingresar el total acumulado producido o agregar solamente "
            "la producción realizada en esta fecha."
        )

        load_mode = st.radio(
            "Forma de carga",
            [
                "Ingresar saldo producido acumulado",
                "Agregar producción del día",
            ],
            horizontal=True,
        )

        with st.form(
            f"production_movement_form_{selected_id}"
        ):
            p1, p2 = st.columns(2)

            with p1:
                prod_date = st.date_input(
                    "Fecha de producción",
                    value=date.today(),
                    key=f"prod_date_{selected_id}",
                )

                if load_mode == "Ingresar saldo producido acumulado":
                    entered_qty = st.number_input(
                        f"Saldo producido acumulado [{row['unidad']}]",
                        min_value=0.0,
                        value=float(produced),
                        step=1.0,
                        key=f"produced_balance_{selected_id}",
                        help=(
                            "Ingresá cuánto lleva producido el lote en total. "
                            "El sistema calcula automáticamente la diferencia "
                            "contra el saldo ya registrado."
                        ),
                    )

                    delta_preview = float(entered_qty) - float(produced)

                    if abs(delta_preview) < 1e-9:
                        st.info(
                            "El saldo ingresado coincide con el saldo actualmente registrado."
                        )
                    elif delta_preview > 0:
                        st.success(
                            f"Se agregarán {delta_preview:,.3f} {row['unidad']} "
                            "al registro de producción."
                        )
                    else:
                        st.warning(
                            f"Se corregirá el saldo en {delta_preview:,.3f} {row['unidad']}."
                        )

                else:
                    entered_qty = st.number_input(
                        f"Producción de esta fecha [{row['unidad']}]",
                        min_value=0.0,
                        value=float(remaining if remaining > 0 else 0.0),
                        step=1.0,
                        key=f"daily_produced_{selected_id}",
                    )

            with p2:
                new_status = st.selectbox(
                    "Estado después del registro",
                    ESTADOS,
                    index=ESTADOS.index(
                        "En producción"
                        if row["estado"] in ("Planificada", "En preparación")
                        else row["estado"]
                    ),
                    key=f"production_status_{selected_id}",
                )

                close_order = st.checkbox(
                    "Cerrar / finalizar lote con este registro",
                    value=False,
                    key=f"close_with_production_{selected_id}",
                )

                if close_order:
                    close_deadline = get_order_close_deadline(row)
                    if close_deadline:
                        st.caption(
                            f"Cierre máximo permitido: "
                            f"{close_deadline.strftime('%d/%m/%Y')}"
                        )

            note = st.text_area(
                "Observación del registro",
                key=f"production_note_{selected_id}",
            )

            save_prod = st.form_submit_button(
                "💾 Guardar saldo producido",
                type="primary",
                use_container_width=True,
            )

        if save_prod:

            if load_mode == "Ingresar saldo producido acumulado":
                movement_qty = float(entered_qty) - float(produced)
                new_total = float(entered_qty)
                event_detail = (
                    f"Saldo acumulado corregido de {produced:.3f} "
                    f"a {new_total:.3f} {row['unidad']}."
                )
            else:
                movement_qty = float(entered_qty)
                new_total = float(produced) + float(entered_qty)
                event_detail = (
                    f"Producción del día: {movement_qty:.3f} {row['unidad']}. "
                    f"Nuevo acumulado: {new_total:.3f} {row['unidad']}."
                )

            # Validar fecha de cierre mensual.
            requested_status = (
                "Finalizada"
                if close_order
                else new_status
            )

            close_deadline = get_order_close_deadline(row)

            if (
                requested_status == "Finalizada"
                and close_deadline
                and prod_date > close_deadline
            ):
                st.error(
                    f"No se guardó el registro. La fecha máxima de cierre "
                    f"de esta orden es {close_deadline.strftime('%d/%m/%Y')}."
                )

            elif (
                load_mode == "Agregar producción del día"
                and float(entered_qty) <= 0
            ):
                st.error(
                    "Ingresá una cantidad mayor que cero para agregar producción."
                )

            else:
                # En modo saldo, un delta 0 es válido como confirmación,
                # pero no generamos un movimiento inútil.
                if abs(movement_qty) > 1e-9:
                    execute(
                        """
                        INSERT INTO movimientos_produccion (
                            orden_id,
                            fecha,
                            cantidad,
                            observacion,
                            created_at
                        )
                        VALUES (?, ?, ?, ?, ?)
                        """,
                        (
                            int(selected_id),
                            prod_date.isoformat(),
                            float(movement_qty),
                            (
                                note.strip()
                                + (
                                    " · Ajuste de saldo acumulado"
                                    if load_mode
                                    == "Ingresar saldo producido acumulado"
                                    else ""
                                )
                            ).strip(" ·"),
                            datetime.now().isoformat(
                                timespec="seconds"
                            ),
                        ),
                    )

                # Guardar explícitamente el saldo final para evitar que quede en 0.
                execute(
                    """
                    UPDATE ordenes
                    SET cantidad_producida = ?,
                        cantidad_real_producida = ?,
                        updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        float(new_total),
                        float(new_total),
                        datetime.now().isoformat(
                            timespec="seconds"
                        ),
                        int(selected_id),
                    ),
                )

                final_status = requested_status

                fecha_fin = None
                expiry_date = None

                if final_status == "Finalizada":
                    fecha_fin = prod_date.isoformat()

                    shelf_months = int(
                        row.get("vida_util_meses")
                        if pd.notna(
                            row.get("vida_util_meses")
                        )
                        else DEFAULT_SHELF_LIFE_MONTHS
                    )

                    expiry_date = months_after(
                        prod_date,
                        shelf_months,
                    ).isoformat()

                execute(
                    """
                    UPDATE ordenes
                    SET estado = ?,
                        fecha_fin = ?,
                        fecha_vencimiento = ?,
                        updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        final_status,
                        fecha_fin,
                        expiry_date,
                        datetime.now().isoformat(
                            timespec="seconds"
                        ),
                        int(selected_id),
                    ),
                )

                add_event(
                    int(selected_id),
                    "Producción real actualizada",
                    event_detail,
                )

                # Verificación inmediata desde SQLite.
                saved = get_order_by_id(
                    int(selected_id)
                )

                saved_real = float(
                    saved.get("cantidad_real_producida")
                    if saved
                    and pd.notna(
                        saved.get("cantidad_real_producida")
                    )
                    else 0
                )

                if abs(saved_real - new_total) < 1e-6:
                    st.success(
                        f"Producción guardada correctamente: "
                        f"{saved_real:,.3f} {row['unidad']}."
                    )
                    st.session_state[
                        "last_production_order"
                    ] = int(selected_id)
                    st.rerun()
                else:
                    st.error(
                        "La base no confirmó el nuevo saldo producido. "
                        "No se recargó la pantalla para evitar ocultar el error."
                    )

        # ====================================================
        # MATERIAS PRIMAS - DESPUÉS DE LA CARGA DE PRODUCCIÓN
        # ====================================================

        theoretical_consumption = calculate_theoretical_consumption(
            row
        )

        if not theoretical_consumption.empty:

            st.subheader(
                "Consumo de materias primas · teórico vs real"
            )

            actual_consumption = get_actual_consumption(
                int(selected_id)
            )

            compare = theoretical_consumption[
                [
                    "materia_prima_id",
                    "mp_codigo",
                    "materia_prima",
                    "densidad",
                    "porcentaje_mm",
                    "unidad",
                    "consumo_teorico",
                    "consumo_teorico_l",
                ]
            ].copy()

            if not actual_consumption.empty:
                compare = compare.merge(
                    actual_consumption[
                        [
                            "materia_prima_id",
                            "consumo_real",
                        ]
                    ],
                    on="materia_prima_id",
                    how="left",
                )
            else:
                compare[
                    "consumo_real"
                ] = 0.0

            compare["consumo_real"] = pd.to_numeric(
                compare["consumo_real"],
                errors="coerce",
            ).fillna(0)

            compare["desvio_pct"] = (
                (
                    compare["consumo_real"]
                    - compare["consumo_teorico"]
                )
                / compare[
                    "consumo_teorico"
                ].replace(0, pd.NA)
                * 100
            )

            st.dataframe(
                compare[
                    [
                        "mp_codigo",
                        "materia_prima",
                        "densidad",
                        "porcentaje_mm",
                        "consumo_teorico",
                        "consumo_teorico_l",
                        "consumo_real",
                        "unidad",
                        "desvio_pct",
                    ]
                ].rename(
                    columns={
                        "mp_codigo":
                            "Código",
                        "materia_prima":
                            "Materia prima",
                        "densidad":
                            "Densidad kg/L",
                        "porcentaje_mm":
                            "% m/m",
                        "consumo_teorico":
                            "Teórico kg",
                        "consumo_teorico_l":
                            "Teórico L",
                        "consumo_real":
                            "Real consumido",
                        "unidad":
                            "Unidad",
                        "desvio_pct":
                            "Desvío %",
                    }
                ).style.format(
                    {
                        "Densidad kg/L":
                            "{:.3f}",
                        "% m/m":
                            "{:.1f}%",
                        "Teórico kg":
                            "{:.3f}",
                        "Teórico L":
                            "{:.3f}",
                        "Real consumido":
                            "{:.3f}",
                        "Desvío %":
                            "{:+.1f}%",
                    },
                    na_rep="—",
                ),
                use_container_width=True,
                hide_index=True,
            )


        st.subheader("Registrar consumo real de materias primas")
        st.caption(
            "El stock se descuenta por la formulación teórica asignada a la orden. "
            "Este registro documenta el consumo real y permite comparar desvíos sin duplicar el descuento."
        )

        theoretical_consumption = calculate_theoretical_consumption(row)

        if theoretical_consumption.empty:
            st.info("La orden no tiene una formulación vinculada.")
        else:
            mp_ids = theoretical_consumption["materia_prima_id"].astype(int).tolist()
            mp_labels = {
                int(r["materia_prima_id"]): f"{r['mp_codigo']} · {r['materia_prima']}"
                for _, r in theoretical_consumption.iterrows()
            }

            with st.form(f"raw_material_consumption_{selected_id}"):

                mc1, mc2 = st.columns(2)

                with mc1:
                    selected_mp_id = st.selectbox(
                        "Materia prima",
                        options=mp_ids,
                        format_func=lambda mid: mp_labels[mid],
                    )

                    mp_row = theoretical_consumption[
                        theoretical_consumption["materia_prima_id"] == selected_mp_id
                    ].iloc[0]

                    st.caption(
                        f"Teórico total para esta orden: "
                        f"{float(mp_row['consumo_teorico']):.3f} {mp_row['unidad']}"
                    )

                    real_mp_qty = st.number_input(
                        f"Cantidad real consumida [{mp_row['unidad']}]",
                        min_value=0.0,
                        value=0.0,
                        step=0.1,
                    )

                with mc2:
                    mp_date = st.date_input(
                        "Fecha de consumo",
                        value=date.today(),
                        key=f"mp_date_{selected_id}",
                    )

                    mp_lot = st.text_input(
                        "Lote de materia prima",
                        placeholder="Lote proveedor / interno",
                    )

                mp_note = st.text_area(
                    "Observación",
                    key=f"mp_note_{selected_id}",
                )

                save_mp = st.form_submit_button(
                    "Guardar consumo real",
                    type="primary",
                    use_container_width=True,
                )

            if save_mp:
                execute(
                    """
                    INSERT INTO consumos_materias_primas (
                        orden_id,
                        materia_prima_id,
                        fecha,
                        cantidad_real,
                        unidad,
                        lote_mp,
                        observacion,
                        created_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        int(selected_id),
                        int(selected_mp_id),
                        mp_date.isoformat(),
                        float(real_mp_qty),
                        str(mp_row["unidad"]),
                        mp_lot.strip(),
                        mp_note.strip(),
                        datetime.now().isoformat(timespec="seconds"),
                    ),
                )

                add_event(
                    int(selected_id),
                    "Consumo de materia prima",
                    (
                        f"{mp_labels[selected_mp_id]} · "
                        f"{real_mp_qty} {mp_row['unidad']} · "
                        f"Lote MP: {mp_lot or '—'}"
                    ),
                )

                st.success("Consumo real registrado.")
                st.rerun()



        st.subheader("Registrar consumibles / embalajes utilizados")

        consumables_available = get_consumables(active_only=True)

        if consumables_available.empty:
            st.info("No hay consumibles activos.")
        else:
            cons_ids = consumables_available["id"].astype(int).tolist()

            with st.form(f"consumable_usage_{selected_id}"):

                cu1, cu2 = st.columns(2)

                with cu1:
                    cons_id = st.selectbox(
                        "Consumible / embalaje",
                        options=cons_ids,
                        format_func=lambda cid: (
                            f"{consumables_available.loc[consumables_available['id'] == cid, 'codigo'].iloc[0]} · "
                            f"{consumables_available.loc[consumables_available['id'] == cid, 'descripcion'].iloc[0]}"
                        ),
                    )

                    cons_qty = st.number_input(
                        "Cantidad utilizada",
                        min_value=0.0,
                        value=0.0,
                        step=1.0,
                    )

                    cons_unit = st.selectbox(
                        "Unidad de consumo",
                        ["unidades", "kg", "g", "L", "mL", "m"],
                    )

                with cu2:
                    cons_date = st.date_input(
                        "Fecha de uso",
                        value=date.today(),
                        key=f"cons_date_{selected_id}",
                    )

                    cons_lot = st.text_input(
                        "Lote / referencia del consumible"
                    )

                cons_note = st.text_area(
                    "Observación",
                    key=f"cons_note_{selected_id}",
                )

                save_cons_usage = st.form_submit_button(
                    "Guardar consumo",
                    type="primary",
                    use_container_width=True,
                )

            if save_cons_usage:
                execute(
                    """
                    INSERT INTO consumos_consumibles (
                        orden_id,
                        consumible_id,
                        fecha,
                        cantidad_real,
                        unidad,
                        lote_consumible,
                        observacion,
                        created_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        int(selected_id),
                        int(cons_id),
                        cons_date.isoformat(),
                        float(cons_qty),
                        cons_unit,
                        cons_lot.strip(),
                        cons_note.strip(),
                        datetime.now().isoformat(timespec="seconds"),
                    ),
                )

                add_event(
                    int(selected_id),
                    "Consumo de consumible",
                    (
                        f"{consumables_available.loc[consumables_available['id'] == cons_id, 'codigo'].iloc[0]} · "
                        f"{cons_qty} {cons_unit}"
                    ),
                )

                st.success("Consumo de consumible registrado.")
                st.rerun()


# ============================================================
# ÓRDENES Y LOTES
# ============================================================

elif section == "Órdenes y lotes":

    st.subheader("Órdenes internas y lotes")

    orders = get_orders()

    if orders.empty:
        st.info("Todavía no existen órdenes.")
    else:

        c1, c2, c3 = st.columns(3)

        with c1:
            family_codes = get_families(active_only=False)["codigo"].astype(str).tolist()
            filter_line = st.selectbox(
                "Familia",
                ["Todas"] + family_codes,
            )

        with c2:
            filter_status = st.selectbox(
                "Estado",
                ["Todos"] + ESTADOS,
            )

        with c3:
            text_filter = st.text_input(
                "Buscar producto / lote / orden"
            )

        view = orders.copy()

        if filter_line != "Todas":
            view = view[
                view["linea"] == filter_line
            ]

        if filter_status != "Todos":
            view = view[
                view["estado"] == filter_status
            ]

        if text_filter.strip():
            q = text_filter.strip().lower()
            mask = (
                view["producto"].astype(str).str.lower().str.contains(q, na=False)
                | view["lote_codigo"].astype(str).str.lower().str.contains(q, na=False)
                | view["orden_codigo"].astype(str).str.lower().str.contains(q, na=False)
            )
            view = view[mask]

        display = view[
            [
                "orden_codigo",
                "lote_codigo",
                "linea",
                "producto",
                "cantidad_planificada",
                "cantidad_producida",
                "unidad",
                "fecha_orden",
                "fecha_inicio",
                "fecha_fin",
                "fecha_vencimiento",
                "responsable",
                "prioridad",
                "estado",
            ]
        ].copy()

        display.columns = [
            "Orden interna",
            "Lote",
            "Familia",
            "Producto",
            "Planificado",
            "Producido",
            "Unidad",
            "Fecha orden",
            "Inicio",
            "Fin",
            "Vencimiento",
            "Responsable",
            "Prioridad",
            "Estado",
        ]

        st.dataframe(
            display,
            use_container_width=True,
            hide_index=True,
        )

        csv_data = display.to_csv(
            index=False,
            sep=";",
            decimal=",",
        ).encode("utf-8-sig")

        st.download_button(
            "⬇️ Descargar órdenes y lotes",
            data=csv_data,
            file_name="sev_ordenes_y_lotes.csv",
            mime="text/csv",
        )

        st.divider()
        st.subheader("Detalle y trazabilidad")

        order_options = view["id"].astype(int).tolist()
        preferred_order = st.session_state.pop("edit_order_id", None)
        default_order_index = (
            order_options.index(int(preferred_order))
            if preferred_order is not None and int(preferred_order) in order_options
            else 0
        )

        selected_order = st.selectbox(
            "Seleccionar orden",
            options=order_options,
            index=default_order_index,
            format_func=lambda oid: (
                f"{view.loc[view['id'] == oid, 'lote_codigo'].iloc[0]} · "
                f"{view.loc[view['id'] == oid, 'producto'].iloc[0]}"
            ),
        )

        if selected_order:

            detail_db_top = get_order_by_id(int(selected_order))
            if detail_db_top:
                detail = pd.Series(detail_db_top)
            else:
                detail = view[
                    view["id"] == selected_order
                ].iloc[0]

            # Mensaje persistente después del rerun de un guardado.
            saved_notice = st.session_state.pop(
                "order_saved_notice",
                None,
            )
            if saved_notice and int(saved_notice.get("order_id", -1)) == int(selected_order):
                st.success(saved_notice.get("message", "Cambios guardados."))

            c1, c2, c3, c4 = st.columns(4)

            c1.metric(
                "Lote",
                detail["lote_codigo"],
            )
            c2.metric(
                "Planificado",
                fmt_qty(
                    detail["cantidad_planificada"],
                    detail["unidad"],
                ),
            )
            c3.metric(
                "Producido",
                fmt_qty(
                    (
                        detail["cantidad_real_producida"]
                        if pd.notna(detail.get("cantidad_real_producida"))
                        else detail["cantidad_producida"]
                    ),
                    detail["unidad"],
                ),
            )
            c4.metric(
                "Estado",
                detail["estado"],
            )

            moves = fetch_df(
                """
                SELECT fecha, cantidad, observacion, created_at
                FROM movimientos_produccion
                WHERE orden_id = ?
                ORDER BY fecha, id
                """,
                (int(selected_order),),
            )

            if not moves.empty:
                st.write("Movimientos de producción")
                st.dataframe(
                    moves.rename(
                        columns={
                            "fecha": "Fecha",
                            "cantidad": "Cantidad",
                            "observacion": "Observación",
                            "created_at": "Registrado",
                        }
                    ),
                    use_container_width=True,
                    hide_index=True,
                )


            used_consumables = get_consumable_usage(
                int(selected_order)
            )

            if not used_consumables.empty:
                st.write("Consumibles / embalajes utilizados")

                st.dataframe(
                    used_consumables[
                        [
                            "fecha",
                            "codigo",
                            "descripcion",
                            "categoria",
                            "cantidad_real",
                            "unidad",
                            "lote_consumible",
                            "observacion",
                        ]
                    ].rename(
                        columns={
                            "fecha": "Fecha",
                            "codigo": "Código",
                            "descripcion": "Descripción",
                            "categoria": "Categoría",
                            "cantidad_real": "Cantidad",
                            "unidad": "Unidad",
                            "lote_consumible": "Lote / referencia",
                            "observacion": "Observación",
                        }
                    ),
                    use_container_width=True,
                    hide_index=True,
                )

            events = fetch_df(
                """
                SELECT fecha, evento, detalle
                FROM eventos
                WHERE orden_id = ?
                ORDER BY id DESC
                """,
                (int(selected_order),),
            )

            if not events.empty:
                st.write("Historial")
                st.dataframe(
                    events.rename(
                        columns={
                            "fecha": "Fecha",
                            "evento": "Evento",
                            "detalle": "Detalle",
                        }
                    ),
                    use_container_width=True,
                    hide_index=True,
                )


            if is_admin():

                st.divider()
                st.subheader("Administración del lote")

                # Volver a consultar la BD para no editar datos viejos del dataframe.
                detail_db = get_order_by_id(int(selected_order))
                if detail_db:
                    detail = pd.Series(detail_db)

                current_status_label = lot_status_label(detail)
                current_status_color = lot_status_color(current_status_label)

                st.markdown(
                    f"""
                    <div style="margin-bottom:12px;">
                        <span class="lot-status" style="background:{current_status_color};margin-left:0;">
                            {current_status_label}
                        </span>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )

                close_deadline = get_order_close_deadline(detail)

                st.caption(
                    f"Orden: {detail['orden_codigo']} · Lote: {detail['lote_codigo']} · "
                    "Los identificadores se mantienen fijos para conservar la trazabilidad."
                )

                if close_deadline:
                    if is_order_close_overdue(detail):
                        st.error(
                            f"ERROR DE CIERRE: esta orden debía cerrarse como máximo el "
                            f"{close_deadline.strftime('%d/%m/%Y')} y continúa abierta."
                        )
                    else:
                        st.info(
                            f"Fecha límite obligatoria de cierre: "
                            f"{close_deadline.strftime('%d/%m/%Y')}."
                        )

                st.markdown("#### Datos actuales guardados")
                s1, s2, s3, s4 = st.columns(4)

                s1.metric(
                    "Cantidad teórica",
                    fmt_qty(
                        detail.get("cantidad_planificada") or 0,
                        detail.get("unidad") or "",
                    ),
                )
                s2.metric(
                    "Cantidad real",
                    fmt_qty(
                        (
                            detail.get("cantidad_real_producida")
                            if pd.notna(detail.get("cantidad_real_producida"))
                            else 0
                        ),
                        detail.get("unidad") or "",
                    ),
                )
                s3.metric(
                    "Finalización",
                    detail.get("fecha_fin") or "Sin fecha",
                )
                s4.metric(
                    "Vencimiento",
                    detail.get("fecha_vencimiento") or "Sin fecha",
                )

                people_admin = get_people(active_only=True)
                families_admin = get_families(active_only=True)
                products_admin = get_products(active_only=True)

                current_resp_email = str(detail.get("responsable_email") or "")
                current_person_match = people_admin[
                    people_admin["email"].astype(str) == current_resp_email
                ]

                default_resp_id = (
                    int(current_person_match.iloc[0]["id"])
                    if not current_person_match.empty
                    else int(people_admin.iloc[0]["id"])
                )

                family_codes = families_admin["codigo"].astype(str).tolist()
                current_family = str(detail.get("linea") or "")
                if current_family not in family_codes and family_codes:
                    current_family = family_codes[0]

                current_product_id = (
                    int(detail["producto_id"])
                    if pd.notna(detail.get("producto_id"))
                    else None
                )

                current_finish = pd.to_datetime(
                    detail.get("fecha_fin"),
                    errors="coerce",
                )

                current_order_date = pd.to_datetime(
                    detail.get("fecha_orden"),
                    errors="coerce",
                )

                current_start = pd.to_datetime(
                    detail.get("fecha_inicio"),
                    errors="coerce",
                )

                with st.form(f"admin_edit_lot_{selected_order}"):

                    e1, e2, e3 = st.columns(3)

                    with e1:
                        edit_family = st.selectbox(
                            "Familia",
                            options=family_codes,
                            index=(
                                family_codes.index(current_family)
                                if current_family in family_codes
                                else 0
                            ),
                            format_func=lambda code: (
                                f"{code} · "
                                f"{families_admin.loc[families_admin['codigo'] == code, 'nombre'].iloc[0]}"
                            ),
                        )

                        family_product_rows = products_admin[
                            products_admin["familia_codigo"].astype(str) == edit_family
                        ].copy()

                        family_product_ids = (
                            family_product_rows["id"].astype(int).tolist()
                            if not family_product_rows.empty
                            else []
                        )

                        if current_product_id not in family_product_ids:
                            current_product_id_for_select = (
                                family_product_ids[0]
                                if family_product_ids
                                else None
                            )
                        else:
                            current_product_id_for_select = current_product_id

                        edit_product_id = st.selectbox(
                            "Producto",
                            options=family_product_ids,
                            index=(
                                family_product_ids.index(current_product_id_for_select)
                                if current_product_id_for_select in family_product_ids
                                else 0
                            ),
                            format_func=lambda pid: (
                                family_product_rows.loc[
                                    family_product_rows["id"] == pid,
                                    "nombre",
                                ].iloc[0]
                            ),
                            disabled=not family_product_ids,
                        )

                        edit_unit_options = ["L", "kg", "unidades"]
                        current_unit = str(detail.get("unidad") or "L")
                        edit_unit = st.selectbox(
                            "Unidad",
                            edit_unit_options,
                            index=(
                                edit_unit_options.index(current_unit)
                                if current_unit in edit_unit_options
                                else 0
                            ),
                        )

                        edit_planned = st.number_input(
                            "Cantidad teórica / planificada",
                            min_value=0.01,
                            value=float(detail["cantidad_planificada"]),
                            step=1.0,
                        )

                        edit_actual = st.number_input(
                            "Cantidad real producida",
                            min_value=0.0,
                            value=float(
                                detail.get("cantidad_real_producida")
                                if pd.notna(detail.get("cantidad_real_producida"))
                                else 0
                            ),
                            step=1.0,
                        )

                    with e2:
                        edit_order_date = st.date_input(
                            "Fecha de orden",
                            value=(
                                current_order_date.date()
                                if pd.notna(current_order_date)
                                else date.today()
                            ),
                        )

                        edit_start = st.date_input(
                            "Fecha de inicio",
                            value=(
                                current_start.date()
                                if pd.notna(current_start)
                                else date.today()
                            ),
                        )

                        register_finish = st.checkbox(
                            "La orden tiene fecha de finalización",
                            value=pd.notna(current_finish),
                            help=(
                                "Marcá esta opción para guardar la fecha de finalización. "
                                "La fecha es editable incluso si la orden todavía está abierta."
                            ),
                        )

                        edit_finish = st.date_input(
                            "Fecha de finalización",
                            value=(
                                current_finish.date()
                                if pd.notna(current_finish)
                                else date.today()
                            ),
                            help=(
                                "Debe ser igual o anterior al último día del mes "
                                "en que se creó la orden."
                            ),
                        )

                        current_shelf = int(
                            detail.get("vida_util_meses")
                            if pd.notna(detail.get("vida_util_meses"))
                            else DEFAULT_SHELF_LIFE_MONTHS
                        )

                        edit_shelf = st.number_input(
                            "Vida útil desde finalización [meses]",
                            min_value=1,
                            max_value=120,
                            value=current_shelf,
                            step=1,
                        )

                    with e3:
                        admin_person_ids = people_admin["id"].astype(int).tolist()

                        edit_resp_id = st.selectbox(
                            "Responsable",
                            options=admin_person_ids,
                            index=admin_person_ids.index(default_resp_id),
                            format_func=lambda pid: (
                                people_admin.loc[
                                    people_admin["id"] == pid,
                                    "nombre",
                                ].iloc[0]
                            ),
                        )

                        edit_status = st.selectbox(
                            "Estado",
                            ESTADOS,
                            index=(
                                ESTADOS.index(detail["estado"])
                                if detail["estado"] in ESTADOS
                                else 0
                            ),
                        )

                        edit_priority = st.selectbox(
                            "Prioridad",
                            PRIORIDADES,
                            index=(
                                PRIORIDADES.index(detail["prioridad"])
                                if detail["prioridad"] in PRIORIDADES
                                else 0
                            ),
                        )

                        # Fórmulas del producto seleccionado
                        formulas_for_product = fetch_df(
                            """
                            SELECT id, version, cantidad_base, unidad_base,
                                   densidad_teorica, activa
                            FROM formulaciones
                            WHERE producto_id = ?
                            ORDER BY version DESC
                            """,
                            (int(edit_product_id),),
                        ) if edit_product_id else pd.DataFrame()

                        formula_options = [None]
                        if not formulas_for_product.empty:
                            formula_options += formulas_for_product["id"].astype(int).tolist()

                        current_formula_id = (
                            int(detail["formulacion_id"])
                            if pd.notna(detail.get("formulacion_id"))
                            else None
                        )

                        formula_index = (
                            formula_options.index(current_formula_id)
                            if current_formula_id in formula_options
                            else 0
                        )

                        edit_formula_id = st.selectbox(
                            "Formulación asignada",
                            options=formula_options,
                            index=formula_index,
                            format_func=lambda fid: (
                                "Sin formulación"
                                if fid is None
                                else (
                                    "V"
                                    + str(
                                        int(
                                            formulas_for_product.loc[
                                                formulas_for_product["id"] == fid,
                                                "version",
                                            ].iloc[0]
                                        )
                                    )
                                    + (
                                        " · ACTIVA"
                                        if int(
                                            formulas_for_product.loc[
                                                formulas_for_product["id"] == fid,
                                                "activa",
                                            ].iloc[0]
                                        ) == 1
                                        else ""
                                    )
                                )
                            ),
                        )

                    edit_obs = st.text_area(
                        "Observaciones",
                        value=str(detail.get("observaciones") or ""),
                    )

                    save_edit = st.form_submit_button(
                        "💾 Guardar todas las modificaciones",
                        type="primary",
                        use_container_width=True,
                    )

                if save_edit:
                    proposed_finish = edit_finish if register_finish else None
                    valid_finish, deadline_check = validate_finish_date(
                        detail,
                        proposed_finish,
                    )

                    if edit_status == "Finalizada" and not register_finish:
                        st.error(
                            "Para guardar el estado Finalizada debés marcar "
                            "'La orden tiene fecha de finalización'."
                        )
                    elif not valid_finish:
                        st.error(
                            f"No se guardaron los cambios. La fecha máxima de cierre es "
                            f"{deadline_check.strftime('%d/%m/%Y')}."
                        )
                    elif not family_product_ids or edit_product_id is None:
                        st.error("La familia seleccionada no tiene un producto válido.")
                    else:
                        selected_resp = people_admin[
                            people_admin["id"] == edit_resp_id
                        ].iloc[0]

                        selected_product = products_admin[
                            products_admin["id"] == edit_product_id
                        ].iloc[0]

                        final_date_value = (
                            edit_finish if register_finish else None
                        )

                        # Si el estado es Finalizada y no se marcó fecha, usar hoy.
                        if edit_status == "Finalizada" and final_date_value is None:
                            today_value = date.today()
                            deadline_value = get_order_close_deadline(detail)
                            final_date_value = (
                                deadline_value
                                if deadline_value and today_value > deadline_value
                                else today_value
                            )

                        expiry_value = (
                            months_after(
                                final_date_value,
                                int(edit_shelf),
                            )
                            if final_date_value
                            else None
                        )

                        save_values = {
                            "linea": edit_family,
                            "producto": str(selected_product["nombre"]),
                            "producto_id": int(edit_product_id),
                            "formulacion_id": (
                                int(edit_formula_id)
                                if edit_formula_id is not None
                                else None
                            ),
                            "cantidad_planificada": float(edit_planned),
                            "cantidad_real_producida": float(edit_actual),
                            "unidad": edit_unit,
                            "fecha_orden": edit_order_date.isoformat(),
                            "fecha_inicio": edit_start.isoformat(),
                            "fecha_fin": (
                                final_date_value.isoformat()
                                if final_date_value
                                else None
                            ),
                            "fecha_vencimiento": (
                                expiry_value.isoformat()
                                if expiry_value
                                else None
                            ),
                            "vida_util_meses": int(edit_shelf),
                            "responsable": str(selected_resp["nombre"]),
                            "responsable_email": str(selected_resp["email"]),
                            "estado": edit_status,
                            "prioridad": edit_priority,
                            "observaciones": edit_obs.strip(),
                        }

                        saved_ok, saved, save_msg = save_order_admin_changes(
                            int(selected_order),
                            save_values,
                        )

                        if not saved_ok:
                            st.error(
                                f"No se pudieron guardar los cambios: {save_msg}"
                            )
                            st.stop()

                        stock_ok, stock_msg = sync_order_material_reservation(
                            int(selected_order)
                        )

                        add_event(
                            int(selected_order),
                            "Lote modificado por administrador",
                            (
                                f"Usuario: {st.session_state.get('current_user', 'Administrador')} · "
                                f"Fórmula: {edit_formula_id or 'sin fórmula'} · "
                                f"Stock: {stock_msg}"
                            ),
                        )

                        # Confirmación del guardado con los datos realmente leídos de SQLite.
                        if saved:
                            message = (
                                "Cambios guardados y confirmados en la base. "
                                f"Planificado: {float(saved.get('cantidad_planificada') or 0):,.2f} {saved.get('unidad') or ''} · "
                                f"Real: {float(saved.get('cantidad_real_producida') or 0):,.2f} {saved.get('unidad') or ''} · "
                                f"Inicio: {saved.get('fecha_inicio') or '—'} · "
                                f"Finalización: {saved.get('fecha_fin') or '—'} · "
                                f"Vencimiento: {saved.get('fecha_vencimiento') or '—'}."
                            )

                            if edit_formula_id is not None:
                                if stock_ok:
                                    message += " · Fórmula y stock sincronizados."
                                else:
                                    message += f" · ATENCIÓN stock: {stock_msg}"

                            st.session_state["order_saved_notice"] = {
                                "order_id": int(selected_order),
                                "message": message,
                            }
                            st.session_state["edit_order_id"] = int(selected_order)
                            st.rerun()

                reserved = get_order_reserved_materials(
                    int(selected_order)
                )

                if not reserved.empty:
                    st.subheader("Stock reservado / descontado por esta orden")
                    st.dataframe(
                        reserved.rename(
                            columns={
                                "codigo_totvs": "Código TOTVS",
                                "materia_prima": "Materia prima",
                                "cantidad_teorica": "Cantidad descontada",
                                "unidad": "Unidad",
                                "saldo_actual": "Saldo actual",
                            }
                        )[
                            [
                                "Código TOTVS",
                                "Materia prima",
                                "Cantidad descontada",
                                "Unidad",
                                "Saldo actual",
                            ]
                        ].style.format(
                            {
                                "Cantidad descontada": "{:.3f}",
                                "Saldo actual": "{:.3f}",
                            }
                        ),
                        use_container_width=True,
                        hide_index=True,
                    )

                q1, q2 = st.columns(2)

                with q1:
                    if str(detail["estado"]) != "Finalizada":
                        if st.button(
                            "🔒 Cerrar lote",
                            key=f"close_lot_{selected_order}",
                            type="primary",
                            use_container_width=True,
                        ):
                            today_value = date.today()
                            deadline_value = get_order_close_deadline(detail)
                            finish_date = (
                                deadline_value
                                if deadline_value and today_value > deadline_value
                                else today_value
                            )
                            shelf_months = int(
                                detail.get("vida_util_meses")
                                if pd.notna(detail.get("vida_util_meses"))
                                else DEFAULT_SHELF_LIFE_MONTHS
                            )
                            expiry = months_after(finish_date, shelf_months)

                            execute(
                                """
                                UPDATE ordenes
                                SET estado = 'Finalizada',
                                    fecha_fin = ?,
                                    fecha_vencimiento = ?,
                                    updated_at = ?
                                WHERE id = ?
                                """,
                                (
                                    finish_date.isoformat(),
                                    expiry.isoformat(),
                                    datetime.now().isoformat(timespec="seconds"),
                                    int(selected_order),
                                ),
                            )
                            add_event(
                                int(selected_order),
                                "Lote cerrado por administrador",
                                f"Usuario: {st.session_state.get('current_user', 'Administrador')}",
                            )
                            st.session_state["edit_order_id"] = int(selected_order)
                            st.rerun()

                with q2:
                    if str(detail["estado"]) == "Finalizada":
                        if st.button(
                            "🔓 Reabrir lote",
                            key=f"reopen_lot_{selected_order}",
                            use_container_width=True,
                        ):
                            execute(
                                """
                                UPDATE ordenes
                                SET estado = 'Planificada',
                                    fecha_fin = NULL,
                                    fecha_vencimiento = NULL,
                                    updated_at = ?
                                WHERE id = ?
                                """,
                                (
                                    datetime.now().isoformat(timespec="seconds"),
                                    int(selected_order),
                                ),
                            )
                            add_event(
                                int(selected_order),
                                "Lote reabierto por administrador",
                                f"Usuario: {st.session_state.get('current_user', 'Administrador')}",
                            )
                            st.session_state["edit_order_id"] = int(selected_order)
                            st.rerun()

            else:

                st.caption(
                    "La modificación de lotes está habilitada sólo para usuarios Administrador."
                )


# ============================================================
# FAMILIAS
# ============================================================

elif section == "Familias":

    st.subheader("Maestro de familias de producción")

    st.caption(
        "Cada familia define el código utilizado en el lote: "
        "SEV-PRD-<FAMILIA>-AÑO-NÚMERO."
    )

    with st.form("new_family_form"):

        c1, c2 = st.columns(2)

        with c1:
            family_code = st.text_input(
                "Código de familia",
                placeholder="Ej.: ADJ, FER, BIO, FUN",
                max_chars=8,
            )

        with c2:
            family_name = st.text_input(
                "Nombre de familia",
                placeholder="Ej.: Adyuvantes",
            )

        add_family = st.form_submit_button(
            "Agregar familia",
            type="primary",
            use_container_width=True,
        )

    if add_family:

        code = normalize_family_code(
            family_code
        )

        name = family_name.strip()

        if not code:
            st.error(
                "Ingresá un código de familia."
            )

        elif not name:
            st.error(
                "Ingresá el nombre de la familia."
            )

        else:
            existing = fetch_df(
                """
                SELECT id
                FROM familias
                WHERE UPPER(codigo) = UPPER(?)
                """,
                (code,),
            )

            if not existing.empty:
                st.error(
                    f"La familia {code} ya existe."
                )
            else:
                execute(
                    """
                    INSERT INTO familias (
                        codigo,
                        nombre,
                        activo,
                        created_at
                    )
                    VALUES (?, ?, 1, ?)
                    """,
                    (
                        code,
                        name,
                        datetime.now().isoformat(
                            timespec="seconds"
                        ),
                    ),
                )

                st.success(
                    f"Familia {code} · {name} agregada."
                )
                st.rerun()

    families_all = get_families(
        active_only=False
    )

    if families_all.empty:
        st.info(
            "Todavía no existen familias."
        )

    else:

        families_display = families_all.copy()

        families_display[
            "Estado"
        ] = families_display[
            "activo"
        ].map(
            {
                1: "Activa",
                0: "Inactiva",
            }
        )

        st.dataframe(
            families_display[
                [
                    "codigo",
                    "nombre",
                    "Estado",
                    "created_at",
                ]
            ].rename(
                columns={
                    "codigo": "Código",
                    "nombre": "Familia",
                    "created_at": "Creada",
                }
            ),
            use_container_width=True,
            hide_index=True,
        )


# ============================================================
# PRODUCTOS
# ============================================================

elif section == "Productos":

    st.subheader("Maestro de productos")

    families = get_families(
        active_only=True
    )

    if families.empty:

        st.warning(
            "Primero cargá al menos una familia."
        )

    else:

        family_ids = families[
            "id"
        ].astype(int).tolist()

        with st.form("new_product_form"):

            c1, c2, c3 = st.columns(3)

            with c1:
                p_name = st.text_input(
                    "Producto"
                )

            with c2:
                p_family_id = st.selectbox(
                    "Familia",
                    options=family_ids,
                    format_func=lambda fid: (
                        f"{families.loc[families['id'] == fid, 'codigo'].iloc[0]} · "
                        f"{families.loc[families['id'] == fid, 'nombre'].iloc[0]}"
                    ),
                    key="product_family",
                )

            with c3:
                p_unit = st.selectbox(
                    "Unidad",
                    UNIDADES,
                    key="product_unit",
                )

            add_product = st.form_submit_button(
                "Agregar producto",
                type="primary",
                use_container_width=True,
            )

        if add_product:

            if not p_name.strip():

                st.error(
                    "Ingresá el nombre del producto."
                )

            else:

                selected_family = families[
                    families["id"] == p_family_id
                ].iloc[0]

                family_code = str(
                    selected_family["codigo"]
                )

                execute(
                    """
                    INSERT INTO productos (
                        nombre,
                        linea,
                        familia_id,
                        unidad,
                        activo,
                        created_at
                    )
                    VALUES (?, ?, ?, ?, 1, ?)
                    """,
                    (
                        p_name.strip(),
                        family_code,
                        int(p_family_id),
                        p_unit,
                        datetime.now().isoformat(
                            timespec="seconds"
                        ),
                    ),
                )

                st.success(
                    f"Producto {p_name.strip()} agregado "
                    f"a la familia {family_code}."
                )

                st.rerun()

    products = get_products(
        active_only=False
    )

    if products.empty:

        st.info(
            "Todavía no existen productos cargados."
        )

    else:

        display_products = products.copy()

        display_products[
            "Familia"
        ] = (
            display_products[
                "familia_codigo"
            ].fillna("—")
            + " · "
            + display_products[
                "familia_nombre"
            ].fillna("Sin familia")
        )

        display_products[
            "Activo"
        ] = display_products[
            "activo"
        ].map(
            {
                1: "Sí",
                0: "No",
            }
        )

        st.dataframe(
            display_products[
                [
                    "nombre",
                    "Familia",
                    "unidad",
                    "Activo",
                    "created_at",
                ]
            ].rename(
                columns={
                    "nombre":
                        "Producto",
                    "unidad":
                        "Unidad",
                    "created_at":
                        "Creado",
                }
            ),
            use_container_width=True,
            hide_index=True,
        )




# ============================================================
# MATERIAS PRIMAS
# ============================================================

elif section == "Materias primas":

    st.subheader("Maestro de materias primas")

    st.caption(
        "Código TOTVS · unidad · stock/saldo · densidad · almacén · estado"
    )

    materials = get_raw_materials(active_only=False)

    if materials.empty:
        st.info("Todavía no existen materias primas.")
    else:
        c1, c2, c3 = st.columns(3)

        with c1:
            mp_search = st.text_input(
                "Buscar código o materia prima"
            )

        with c2:
            mp_status_filter = st.selectbox(
                "Estado",
                ["Todas", "Activas", "Inactivas"],
            )

        with c3:
            mp_warehouse_filter = st.selectbox(
                "Almacén",
                ["Todos"] + sorted(
                    [
                        str(x)
                        for x in materials["almacen"].dropna().unique().tolist()
                        if str(x).strip()
                    ]
                ),
            )

        view = materials.copy()

        if mp_search.strip():
            q = mp_search.strip().lower()
            view = view[
                view["nombre"].astype(str).str.lower().str.contains(q, na=False)
                | view["codigo"].astype(str).str.lower().str.contains(q, na=False)
                | view["codigo_totvs"].astype(str).str.lower().str.contains(q, na=False)
            ]

        if mp_status_filter == "Activas":
            view = view[view["activo"] == 1]
        elif mp_status_filter == "Inactivas":
            view = view[view["activo"] == 0]

        if mp_warehouse_filter != "Todos":
            view = view[
                view["almacen"].astype(str) == mp_warehouse_filter
            ]

        display = view.copy()

        display["Estado"] = display["activo"].map(
            {1: "Activa", 0: "Inactiva"}
        )

        display["Código TOTVS"] = display["codigo_totvs"].fillna(
            display["codigo"]
        )

        display["Unidad"] = display["unidad_stock"].fillna(
            display["unidad"]
        )

        display["Saldo actual"] = pd.to_numeric(
            display["saldo_actual"],
            errors="coerce",
        ).fillna(0.0)

        display["Densidad kg/L"] = pd.to_numeric(
            display["densidad"],
            errors="coerce",
        )

        st.dataframe(
            display[
                [
                    "Código TOTVS",
                    "nombre",
                    "Unidad",
                    "almacen",
                    "Saldo actual",
                    "Densidad kg/L",
                    "Estado",
                ]
            ].rename(
                columns={
                    "nombre": "Materia prima",
                    "almacen": "Almacén",
                }
            ).style.format(
                {
                    "Saldo actual": "{:,.3f}",
                    "Densidad kg/L": "{:.3f}",
                },
                na_rep="—",
            ),
            use_container_width=True,
            hide_index=True,
        )

        st.download_button(
            "⬇️ Descargar materias primas",
            data=display[
                [
                    "Código TOTVS",
                    "nombre",
                    "Unidad",
                    "almacen",
                    "Saldo actual",
                    "Densidad kg/L",
                    "Estado",
                ]
            ].rename(
                columns={
                    "nombre": "Materia prima",
                    "almacen": "Almacén",
                }
            ).to_csv(
                index=False,
                sep=";",
            ).encode("utf-8-sig"),
            file_name="sev_materias_primas.csv",
            mime="text/csv",
            use_container_width=True,
        )

    if is_admin():

        st.divider()
        st.subheader("Agregar nueva materia prima")

        with st.form("new_raw_material_form"):
            a1, a2, a3 = st.columns(3)

            with a1:
                mp_code = st.text_input(
                    "Código TOTVS",
                    placeholder="Ej.: MP0020",
                )

                mp_name = st.text_input(
                    "Materia prima"
                )

            with a2:
                mp_unit = st.selectbox(
                    "Unidad de stock",
                    ["kg", "g", "L", "mL", "unidades"],
                )

                mp_warehouse = st.text_input(
                    "Almacén",
                    value="01",
                )

            with a3:
                mp_balance = st.number_input(
                    "Saldo actual",
                    value=0.0,
                    step=1.0,
                )

                mp_density = st.number_input(
                    "Densidad [kg/L]",
                    min_value=0.0,
                    value=0.0,
                    step=0.001,
                    format="%.3f",
                    help="Dejar 0 si todavía no se conoce.",
                )

            add_mp = st.form_submit_button(
                "Agregar materia prima",
                type="primary",
                use_container_width=True,
            )

        if add_mp:
            code = mp_code.strip().upper()
            name = mp_name.strip()

            if not code or not name:
                st.error(
                    "Ingresá Código TOTVS y nombre."
                )
            else:
                existing = fetch_df(
                    """
                    SELECT id
                    FROM materias_primas
                    WHERE UPPER(COALESCE(codigo_totvs, codigo)) = UPPER(?)
                    """,
                    (code,),
                )

                if not existing.empty:
                    st.error(
                        "Ese Código TOTVS ya existe."
                    )
                else:
                    execute(
                        """
                        INSERT INTO materias_primas (
                            codigo,
                            codigo_totvs,
                            nombre,
                            unidad,
                            unidad_stock,
                            almacen,
                            saldo_actual,
                            densidad,
                            activo,
                            created_at
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
                        """,
                        (
                            code,
                            code,
                            name,
                            mp_unit,
                            mp_unit,
                            mp_warehouse.strip(),
                            float(mp_balance),
                            (
                                float(mp_density)
                                if float(mp_density) > 0
                                else None
                            ),
                            datetime.now().isoformat(
                                timespec="seconds"
                            ),
                        ),
                    )

                    st.success(
                        "Materia prima agregada."
                    )
                    st.rerun()

        st.divider()
        st.subheader("Modificar materia prima")

        materials_admin = get_raw_materials(
            active_only=False
        )

        if not materials_admin.empty:

            mp_ids = materials_admin[
                "id"
            ].astype(int).tolist()

            selected_mp_id = st.selectbox(
                "Seleccionar materia prima",
                options=mp_ids,
                format_func=lambda mid: (
                    f"{materials_admin.loc[materials_admin['id'] == mid, 'codigo_totvs'].fillna(materials_admin.loc[materials_admin['id'] == mid, 'codigo']).iloc[0]} · "
                    f"{materials_admin.loc[materials_admin['id'] == mid, 'nombre'].iloc[0]}"
                ),
            )

            mp_detail = materials_admin[
                materials_admin["id"] == selected_mp_id
            ].iloc[0]

            with st.form(
                f"edit_raw_material_{selected_mp_id}"
            ):

                e1, e2, e3 = st.columns(3)

                with e1:
                    edit_code = st.text_input(
                        "Código TOTVS",
                        value=str(
                            mp_detail["codigo_totvs"]
                            if pd.notna(mp_detail["codigo_totvs"])
                            else mp_detail["codigo"]
                        ),
                    )

                    edit_name = st.text_input(
                        "Materia prima",
                        value=str(mp_detail["nombre"]),
                    )

                with e2:
                    current_unit = (
                        str(mp_detail["unidad_stock"])
                        if pd.notna(mp_detail["unidad_stock"])
                        else str(mp_detail["unidad"])
                    )

                    unit_options = [
                        "kg", "g", "L", "mL", "unidades"
                    ]

                    edit_unit = st.selectbox(
                        "Unidad de stock",
                        unit_options,
                        index=(
                            unit_options.index(current_unit)
                            if current_unit in unit_options
                            else 0
                        ),
                    )

                    edit_warehouse = st.text_input(
                        "Almacén",
                        value=(
                            str(mp_detail["almacen"])
                            if pd.notna(mp_detail["almacen"])
                            else ""
                        ),
                    )

                with e3:
                    edit_balance = st.number_input(
                        "Saldo actual",
                        value=float(
                            mp_detail["saldo_actual"]
                            if pd.notna(mp_detail["saldo_actual"])
                            else 0
                        ),
                        step=1.0,
                    )

                    edit_density = st.number_input(
                        "Densidad [kg/L]",
                        min_value=0.0,
                        value=float(
                            mp_detail["densidad"]
                            if pd.notna(mp_detail["densidad"])
                            else 0
                        ),
                        step=0.001,
                        format="%.3f",
                    )

                edit_active = st.checkbox(
                    "Materia prima activa",
                    value=bool(mp_detail["activo"]),
                )

                save_edit_mp = st.form_submit_button(
                    "Guardar cambios",
                    type="primary",
                    use_container_width=True,
                )

            if save_edit_mp:
                execute(
                    """
                    UPDATE materias_primas
                    SET codigo = ?,
                        codigo_totvs = ?,
                        nombre = ?,
                        unidad = ?,
                        unidad_stock = ?,
                        almacen = ?,
                        saldo_actual = ?,
                        densidad = ?,
                        activo = ?
                    WHERE id = ?
                    """,
                    (
                        edit_code.strip().upper(),
                        edit_code.strip().upper(),
                        edit_name.strip(),
                        edit_unit,
                        edit_unit,
                        edit_warehouse.strip(),
                        float(edit_balance),
                        (
                            float(edit_density)
                            if float(edit_density) > 0
                            else None
                        ),
                        1 if edit_active else 0,
                        int(selected_mp_id),
                    ),
                )

                st.success(
                    "Materia prima actualizada."
                )
                st.rerun()




# ============================================================
# CONSUMIBLES
# ============================================================

elif section == "Consumibles":

    st.subheader("Consumibles y embalajes")

    st.caption(
        "Listado inicial cargado desde SIGA/MATA225. "
        "MC = consumibles y EM = embalajes."
    )

    consumables_df = get_consumables(active_only=False)

    if consumables_df.empty:
        st.info("Todavía no existen consumibles cargados.")
    else:
        c1, c2 = st.columns(2)

        with c1:
            category_filter = st.selectbox(
                "Categoría",
                ["Todas", "Consumible", "Embalaje"],
            )

        with c2:
            search_consumable = st.text_input(
                "Buscar código o descripción"
            )

        view = consumables_df.copy()

        if category_filter != "Todas":
            view = view[
                view["categoria"] == category_filter
            ]

        if search_consumable.strip():
            q = search_consumable.strip().lower()
            view = view[
                view["codigo"].astype(str).str.lower().str.contains(q, na=False)
                | view["descripcion"].astype(str).str.lower().str.contains(q, na=False)
            ]

        st.dataframe(
            view[
                [
                    "codigo",
                    "descripcion",
                    "categoria",
                    "armazem",
                    "saldo_referencia",
                ]
            ].rename(
                columns={
                    "codigo": "Código",
                    "descripcion": "Descripción",
                    "categoria": "Categoría",
                    "armazem": "Almacén",
                    "saldo_referencia": "Saldo SIGA ref.",
                }
            ),
            use_container_width=True,
            hide_index=True,
        )

        st.download_button(
            "⬇️ Descargar consumibles",
            data=view.to_csv(
                index=False,
                sep=";",
            ).encode("utf-8-sig"),
            file_name="sev_consumibles.csv",
            mime="text/csv",
        )

    if is_admin():
        st.divider()
        st.subheader("Agregar consumible")

        with st.form("new_consumable_form"):
            cc1, cc2 = st.columns(2)

            with cc1:
                new_cons_code = st.text_input(
                    "Código"
                )
                new_cons_desc = st.text_input(
                    "Descripción"
                )

            with cc2:
                new_cons_cat = st.selectbox(
                    "Categoría",
                    ["Consumible", "Embalaje"],
                )
                new_cons_wh = st.text_input(
                    "Almacén"
                )

            save_cons = st.form_submit_button(
                "Agregar consumible",
                type="primary",
                use_container_width=True,
            )

        if save_cons:
            if not new_cons_code.strip() or not new_cons_desc.strip():
                st.error("Ingresá código y descripción.")
            else:
                execute(
                    """
                    INSERT OR IGNORE INTO consumibles (
                        codigo,
                        descripcion,
                        armazem,
                        categoria,
                        saldo_referencia,
                        activo,
                        created_at
                    )
                    VALUES (?, ?, ?, ?, NULL, 1, ?)
                    """,
                    (
                        new_cons_code.strip().upper(),
                        new_cons_desc.strip(),
                        new_cons_wh.strip(),
                        new_cons_cat,
                        datetime.now().isoformat(timespec="seconds"),
                    ),
                )
                st.success("Consumible agregado.")
                st.rerun()


# ============================================================
# FORMULACIONES
# ============================================================

elif section == "Formulaciones":

    st.subheader("Formulaciones por producto")

    products = get_products(active_only=True)
    materials = get_raw_materials(active_only=True)

    if products.empty:
        st.warning("Primero cargá productos.")
    elif materials.empty:
        st.warning("Primero cargá materias primas.")
    else:
        product_ids = products["id"].astype(int).tolist()

        selected_product_id = st.selectbox(
            "Producto",
            options=product_ids,
            format_func=lambda pid: (
                f"{products.loc[products['id'] == pid, 'familia_codigo'].iloc[0]} · "
                f"{products.loc[products['id'] == pid, 'nombre'].iloc[0]}"
            ),
        )

        selected_product = products[
            products["id"] == selected_product_id
        ].iloc[0]

        active_formula = get_active_formulation(int(selected_product_id))

        if active_formula:
            formula_density = active_formula.get(
                "densidad_teorica"
            )
            formula_volume = active_formula.get(
                "volumen_base_l"
            )

            st.success(
                f"Formulación activa V{active_formula['version']} · "
                f"Base {active_formula['cantidad_base']} {active_formula['unidad_base']} · "
                f"Volumen {float(formula_volume):.3f} L · "
                f"Densidad teórica {float(formula_density):.3f} kg/L"
            )

            items = get_formulation_items(int(active_formula["id"]))

            if not items.empty:
                st.dataframe(
                    items[
                        [
                            "mp_codigo",
                            "materia_prima",
                            "densidad",
                            "cantidad",
                            "cantidad_l",
                            "porcentaje_mm",
                            "unidad",
                            "observacion",
                        ]
                    ].rename(
                        columns={
                            "mp_codigo": "Código",
                            "materia_prima": "Materia prima",
                            "densidad": "Densidad kg/L",
                            "cantidad": "Cantidad kg",
                            "cantidad_l": "Cantidad L",
                            "porcentaje_mm": "% m/m",
                            "unidad": "Unidad base",
                            "observacion": "Observación",
                        }
                    ),
                    use_container_width=True,
                    hide_index=True,
                )
        else:
            st.info("El producto no tiene formulación activa.")

        if is_admin():
            st.divider()
            st.subheader("Crear nueva versión de formulación")

            previous_version = int(active_formula["version"]) if active_formula else 0

            with st.form(f"new_formula_{selected_product_id}"):
                f1, f2, f3, f4 = st.columns(4)

                with f1:
                    base_qty = st.number_input(
                        "Cantidad base",
                        min_value=0.01,
                        value=274.0,
                        step=1.0,
                    )

                with f2:
                    base_unit = st.selectbox(
                        "Unidad base",
                        ["kg", "L"],
                        index=0,
                    )

                with f3:
                    formula_density = st.number_input(
                        "Densidad teórica [kg/L]",
                        min_value=0.001,
                        value=0.913,
                        step=0.001,
                        format="%.3f",
                    )

                with f4:
                    formula_volume = st.number_input(
                        "Volumen base [L]",
                        min_value=0.001,
                        value=300.246,
                        step=0.001,
                        format="%.3f",
                    )

                formula_obs = st.text_area("Observaciones de la formulación")

                create_formula = st.form_submit_button(
                    f"Crear formulación V{previous_version + 1}",
                    type="primary",
                    use_container_width=True,
                )

            if create_formula:
                execute(
                    "UPDATE formulaciones SET activa=0 WHERE producto_id=?",
                    (int(selected_product_id),),
                )

                formula_id = execute(
                    """
                    INSERT INTO formulaciones (
                        producto_id, version, cantidad_base, unidad_base,
                        observaciones, activa, created_at,
                        densidad_teorica, volumen_base_l
                    )
                    VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?)
                    """,
                    (
                        int(selected_product_id),
                        previous_version + 1,
                        float(base_qty),
                        base_unit,
                        formula_obs.strip(),
                        datetime.now().isoformat(timespec="seconds"),
                        float(formula_density),
                        float(formula_volume),
                    ),
                )

                st.session_state["editing_formula_id"] = int(formula_id)
                st.success(
                    f"Formulación V{previous_version + 1} creada. "
                    "Ahora agregá las materias primas."
                )
                st.rerun()

            editing_formula_id = st.session_state.get("editing_formula_id")

            if editing_formula_id:
                formula_edit = fetch_df(
                    "SELECT * FROM formulaciones WHERE id=?",
                    (int(editing_formula_id),),
                )

                if not formula_edit.empty:
                    formula_edit = formula_edit.iloc[0].to_dict()

                    if int(formula_edit["producto_id"]) == int(selected_product_id):

                        st.subheader(
                            f"Agregar materias primas · V{formula_edit['version']}"
                        )

                        material_ids = materials["id"].astype(int).tolist()

                        with st.form(f"add_formula_item_{editing_formula_id}"):

                            i1, i2, i3, i4 = st.columns(4)

                            with i1:
                                material_id = st.selectbox(
                                    "Materia prima",
                                    options=material_ids,
                                    format_func=lambda mid: (
                                        f"{materials.loc[materials['id'] == mid, 'codigo'].iloc[0]} · "
                                        f"{materials.loc[materials['id'] == mid, 'nombre'].iloc[0]}"
                                    ),
                                )

                            selected_material = materials[
                                materials["id"] == material_id
                            ].iloc[0]

                            default_density = (
                                float(selected_material["densidad"])
                                if "densidad" in selected_material.index
                                and pd.notna(selected_material["densidad"])
                                and float(selected_material["densidad"]) > 0
                                else 1.000
                            )

                            with i2:
                                material_density = st.number_input(
                                    "Densidad [kg/L]",
                                    min_value=0.001,
                                    value=default_density,
                                    step=0.001,
                                    format="%.3f",
                                )

                            with i3:
                                material_qty = st.number_input(
                                    "Cantidad [kg]",
                                    min_value=0.0,
                                    value=0.0,
                                    step=0.1,
                                )

                            with i4:
                                material_percent = st.number_input(
                                    "% m/m",
                                    min_value=0.0,
                                    max_value=100.0,
                                    value=0.0,
                                    step=0.1,
                                )

                            material_liters = (
                                float(material_qty) / float(material_density)
                                if float(material_density) > 0
                                else 0.0
                            )

                            st.caption(
                                f"Volumen calculado: {material_liters:.3f} L"
                            )

                            material_note = st.text_input("Observación / función")

                            add_formula_item = st.form_submit_button(
                                "Agregar a la formulación",
                                type="primary",
                                use_container_width=True,
                            )

                        if add_formula_item:
                            existing_item = fetch_df(
                                """
                                SELECT id
                                FROM formulacion_items
                                WHERE formulacion_id=?
                                  AND materia_prima_id=?
                                """,
                                (
                                    int(editing_formula_id),
                                    int(material_id),
                                ),
                            )

                            if not existing_item.empty:
                                st.error("Esa materia prima ya está en la formulación.")
                            else:
                                execute(
                                    """
                                    INSERT INTO formulacion_items (
                                        formulacion_id, materia_prima_id,
                                        cantidad, unidad, observacion,
                                        densidad, cantidad_l, porcentaje_mm
                                    )
                                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                                    """,
                                    (
                                        int(editing_formula_id),
                                        int(material_id),
                                        float(material_qty),
                                        "kg",
                                        material_note.strip(),
                                        float(material_density),
                                        float(material_liters),
                                        float(material_percent),
                                    ),
                                )
                                st.success("Materia prima agregada.")
                                st.rerun()

                        current_items = get_formulation_items(
                            int(editing_formula_id)
                        )

                        if not current_items.empty:
                            st.dataframe(
                                current_items[
                                    [
                                        "mp_codigo",
                                        "materia_prima",
                                        "densidad",
                                        "cantidad",
                                        "cantidad_l",
                                        "porcentaje_mm",
                                        "unidad",
                                        "observacion",
                                    ]
                                ].rename(
                                    columns={
                                        "mp_codigo": "Código",
                                        "materia_prima": "Materia prima",
                                        "densidad": "Densidad kg/L",
                                        "cantidad": "Cantidad kg",
                                        "cantidad_l": "Cantidad L",
                                        "porcentaje_mm": "% m/m",
                                        "unidad": "Unidad base",
                                        "observacion": "Observación",
                                    }
                                ),
                                use_container_width=True,
                                hide_index=True,
                            )

                            if st.button(
                                "Finalizar edición de formulación",
                                type="primary",
                                use_container_width=True,
                            ):
                                st.session_state.pop("editing_formula_id", None)
                                st.success("Formulación lista para utilizar.")
                                st.rerun()


# ============================================================
# PERSONAS Y CORREOS
# ============================================================

elif section == "Personas y correos":

    st.subheader("Personas y correos SEV")

    people = get_people(
        active_only=False
    )

    if people.empty:
        st.info(
            "No existen personas cargadas."
        )
    else:

        display_people = people.copy()

        display_people[
            "Estado"
        ] = display_people[
            "activo"
        ].map(
            {
                1: "Activo",
                0: "Inactivo",
            }
        )

        st.dataframe(
            display_people[
                [
                    "nombre",
                    "email",
                    "rol",
                    "Estado",
                ]
            ].rename(
                columns={
                    "nombre":
                        "Persona",
                    "email":
                        "Correo",
                    "rol":
                        "Rol",
                }
            ),
            use_container_width=True,
            hide_index=True,
        )

    if is_admin():

        st.divider()
        st.subheader(
            "Agregar persona"
        )

        with st.form(
            "new_person_form"
        ):

            p1, p2, p3 = st.columns(3)

            with p1:
                new_name = st.text_input(
                    "Nombre"
                )

            with p2:
                new_email = st.text_input(
                    "Correo"
                )

            with p3:
                new_role = st.selectbox(
                    "Rol",
                    [
                        "Administrador",
                        "Responsable",
                    ],
                )

            add_person = st.form_submit_button(
                "Agregar persona",
                type="primary",
                use_container_width=True,
            )

        if add_person:

            if (
                not new_name.strip()
                or not new_email.strip()
                or "@" not in new_email
            ):

                st.error(
                    "Ingresá nombre y un correo válido."
                )

            else:

                existing = fetch_df(
                    """
                    SELECT id
                    FROM personas
                    WHERE LOWER(email) = LOWER(?)
                    """,
                    (
                        new_email.strip(),
                    ),
                )

                if not existing.empty:
                    st.error(
                        "Ese correo ya está registrado."
                    )
                else:
                    execute(
                        """
                        INSERT INTO personas (
                            nombre,
                            email,
                            rol,
                            activo,
                            created_at
                        )
                        VALUES (?, ?, ?, 1, ?)
                        """,
                        (
                            new_name.strip(),
                            new_email.strip(),
                            new_role,
                            datetime.now().isoformat(
                                timespec="seconds"
                            ),
                        ),
                    )

                    st.success(
                        "Persona agregada correctamente."
                    )
                    st.rerun()

    else:

        st.caption(
            "Sólo un Administrador puede agregar personas o modificar correos."
        )



# ============================================================
# BACKUP / RECUPERACIÓN
# ============================================================

elif section == "Backup / recuperación":

    st.subheader("Backup persistente y recuperación")

    if PERSISTENCE_ENABLED:
        st.success(
            "Persistencia remota configurada. "
            "La base se restaura automáticamente cuando Streamlit inicia "
            "un contenedor nuevo."
        )
    else:
        st.error(
            "Persistencia remota todavía NO configurada. "
            "Agregá GITHUB_TOKEN en Secrets antes de volver a cargar información."
        )

    c1, c2, c3 = st.columns(3)

    orders_now = fetch_df("SELECT COUNT(*) AS n FROM ordenes")
    order_count = int(orders_now.iloc[0]["n"]) if not orders_now.empty else 0

    with c1:
        st.metric("Órdenes en base actual", order_count)

    with c2:
        size_mb = DB_PATH.stat().st_size / 1024 / 1024 if DB_PATH.exists() else 0
        st.metric("Tamaño base", f"{size_mb:.2f} MB")

    with c3:
        last_ok = st.session_state.get("last_backup_ok", "—")
        st.metric("Último backup de sesión", last_ok)

    st.divider()
    st.markdown("### Crear copia ahora")

    if st.button(
        "☁️ Guardar backup persistente ahora",
        type="primary",
        use_container_width=True,
    ):
        ok, msg = backup_db_to_github(reason="backup manual")
        if ok:
            st.success(msg)
        else:
            st.error(msg)

    if DB_PATH.exists():
        st.download_button(
            "⬇️ Descargar sev_produccion.db",
            data=DB_PATH.read_bytes(),
            file_name=(
                "sev_produccion_"
                + datetime.now().strftime("%Y%m%d_%H%M")
                + ".db"
            ),
            mime="application/octet-stream",
            use_container_width=True,
        )

    st.divider()
    st.markdown("### Recuperar desde GitHub")

    st.caption(
        "Este botón reemplaza la base local por la última copia remota. "
        "Úsalo si el tablero aparece vacío después de un reinicio."
    )

    if st.button(
        "♻️ Restaurar última copia remota",
        use_container_width=True,
    ):
        ok, msg = restore_db_from_github(force=True)
        if ok:
            st.success(msg)
            st.rerun()
        else:
            st.error(msg)

    st.divider()
    st.markdown("### Recuperar una base SQLite anterior")

    uploaded_db = st.file_uploader(
        "Subir archivo sev_produccion.db",
        type=["db", "sqlite", "sqlite3"],
        key="restore_old_db",
    )

    if uploaded_db is not None:
        raw = uploaded_db.getvalue()
        valid, counts, msg = validate_uploaded_sqlite(raw)

        if valid:
            st.success(msg)
            st.write("Contenido detectado:")
            st.json(counts)

            confirm_restore = st.checkbox(
                "Confirmo que quiero reemplazar la base actual por este archivo.",
                key="confirm_restore_db",
            )

            if st.button(
                "Restaurar archivo cargado",
                disabled=not confirm_restore,
                type="primary",
                use_container_width=True,
            ):
                # Crear copia de seguridad de la base actual antes de reemplazar.
                if DB_PATH.exists():
                    backup_db_to_github(reason="antes de restauración manual")

                DB_PATH.write_bytes(raw)

                # Guardar inmediatamente la base restaurada en remoto.
                ok_remote, msg_remote = backup_db_to_github(
                    reason="restauración manual"
                )
                st.success("Base restaurada correctamente.")
                if ok_remote:
                    st.success("La copia restaurada quedó guardada en GitHub.")
                else:
                    st.warning(msg_remote)
                st.rerun()
        else:
            st.error(msg)

    st.divider()
    st.markdown("### Estado técnico")

    st.write(
        {
            "Repositorio backup": GITHUB_REPO if GITHUB_REPO else "No configurado",
            "Rama": GITHUB_BRANCH,
            "Archivo remoto": GITHUB_DB_BACKUP_PATH,
            "Persistencia activa": PERSISTENCE_ENABLED,
            "Error último backup": st.session_state.get(
                "last_backup_error",
                "",
            ),
        }
    )

    st.info(
        "La V1.15 mantiene SQLite para conservar todas las funciones actuales, "
        "pero sincroniza la base comprimida con el repositorio privado. "
        "Así un nuevo deploy o reinicio puede restaurar automáticamente los datos."
    )


# ============================================================
# PIE
# ============================================================

st.divider()
st.caption(
    f"SEV | Control de Producción · {APP_VERSION} · "
    "Órdenes, lotes, formulaciones gravimétricas, materias primas, consumibles y trazabilidad"
)
