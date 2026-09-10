
# ============================================================
# SEV | CONTROL DE PRODUCCIÓN
# V1.0
# ============================================================
# Streamlit + SQLite
# Gestión de órdenes internas, lotes y cantidades producidas.
# ============================================================

from __future__ import annotations

import sqlite3
import smtplib
from email.message import EmailMessage
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import quote

import pandas as pd
import streamlit as st


# ============================================================
# CONFIGURACIÓN
# ============================================================

APP_VERSION = "V1.2"
APP_TITLE = "SEV | Control de Producción"

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "sev_produccion.db"

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
    ]:
        if column_name not in order_columns:
            cur.execute(
                f"ALTER TABLE ordenes ADD COLUMN {column_name} {column_type}"
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
        return cur.lastrowid
    finally:
        conn.close()


init_db()


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
        SET cantidad_producida = ?, updated_at = ?
        WHERE id = ?
        """,
        (
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
        <p>{APP_VERSION} · Órdenes internas · Lotes · Fechas · Vencimientos · Producción · Trazabilidad</p>
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

    section = st.radio(
        "Módulo",
        [
            "Tablero",
            "Nueva orden",
            "Registrar producción",
            "Órdenes y lotes",
            "Familias",
            "Productos",
            "Personas y correos",
        ],
        index=0,
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

    c1, c2, c3, c4, c5, c6 = st.columns(6)

    c1.metric("Órdenes totales", total_orders)
    c2.metric("Órdenes activas", active_orders)
    c3.metric("Finalizadas", finished_orders)
    c4.metric("Cantidad producida", f"{total_qty:,.1f}".replace(",", "."))
    c5.metric("Vencen ≤ 90 días", expiring_90)
    c6.metric("Vencidos", expired)

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
                produced = float(row["cantidad_producida"] or 0)
                progress = (produced / planned * 100) if planned > 0 else 0

                st.markdown(
                    f"""
                    <div class="sev-box">
                        <div class="sev-lot">{row['lote_codigo']}</div>
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

    st.subheader("Tablero de lotes")

    if not orders.empty:
        board = orders[
            [
                "lote_codigo",
                "orden_codigo",
                "producto",
                "linea",
                "cantidad_planificada",
                "cantidad_producida",
                "unidad",
                "fecha_inicio",
                "fecha_fin",
                "fecha_vencimiento",
                "responsable",
                "estado",
                "prioridad",
            ]
        ].copy()

        board.columns = [
            "Lote",
            "Orden",
            "Producto",
            "Familia",
            "Planificado",
            "Producido",
            "Unidad",
            "Inicio",
            "Finalización",
            "Vencimiento",
            "Responsable",
            "Estado",
            "Prioridad",
        ]

        st.dataframe(
            board,
            use_container_width=True,
            hide_index=True,
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

                unidad = st.selectbox(
                    "Unidad",
                    UNIDADES,
                    index=UNIDADES.index(unidad_default)
                    if unidad_default in UNIDADES
                    else 0,
                )

                cantidad = st.number_input(
                    "Cantidad planificada",
                    min_value=0.01,
                    value=250.0,
                    step=10.0,
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

            st.info(
                f"Orden propuesta: **{order_preview}**  ·  "
                f"Lote propuesto: **{lot_preview}**"
            )

            submitted = st.form_submit_button(
                "Crear orden y lote",
                type="primary",
                use_container_width=True,
                disabled=(product_id is None),
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
                    created_at,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, 0, ?, ?, ?, NULL, ?, ?, ?, 'Planificada', ?, ?, NULL, ?, ?)
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
                    now,
                    now,
                ),
            )

            add_event(
                order_id,
                "Orden creada",
                f"{order_code} · {lot_code} · {producto}",
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

        row = active[
            active["id"] == selected_id
        ].iloc[0]

        planned = float(row["cantidad_planificada"] or 0)
        produced = float(row["cantidad_producida"] or 0)
        remaining = max(planned - produced, 0)

        c1, c2, c3 = st.columns(3)
        c1.metric(
            "Planificado",
            fmt_qty(planned, row["unidad"]),
        )
        c2.metric(
            "Producido",
            fmt_qty(produced, row["unidad"]),
        )
        c3.metric(
            "Pendiente",
            fmt_qty(remaining, row["unidad"]),
        )

        st.markdown(
            f"""
            <div class="sev-box">
                <div class="sev-lot">{row['lote_codigo']}</div>
                Orden interna: <b>{row['orden_codigo']}</b><br>
                Producto: <b>{row['producto']}</b><br>
                Responsable: {row['responsable'] or '—'}
            </div>
            """,
            unsafe_allow_html=True,
        )

        with st.form("production_movement_form"):

            c1, c2 = st.columns(2)

            with c1:
                prod_date = st.date_input(
                    "Fecha de producción",
                    value=date.today(),
                )

                qty = st.number_input(
                    f"Cantidad producida [{row['unidad']}]",
                    min_value=0.01,
                    value=float(remaining if remaining > 0 else 1.0),
                    step=1.0,
                )

            with c2:
                new_status = st.selectbox(
                    "Estado después del registro",
                    ESTADOS,
                    index=ESTADOS.index(
                        "En producción"
                        if row["estado"] in ("Planificada", "En preparación")
                        else row["estado"]
                    ),
                )

                close_order = st.checkbox(
                    "Marcar como finalizada",
                    value=False,
                )

            note = st.text_area(
                "Observación del registro"
            )

            save_prod = st.form_submit_button(
                "Guardar producción",
                type="primary",
                use_container_width=True,
            )

        if save_prod:

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
                    float(qty),
                    note.strip(),
                    datetime.now().isoformat(timespec="seconds"),
                ),
            )

            update_total_produced(
                int(selected_id)
            )

            final_status = (
                "Finalizada"
                if close_order
                else new_status
            )

            fecha_fin = (
                prod_date.isoformat()
                if final_status == "Finalizada"
                else None
            )

            expiry_date = None

            if final_status == "Finalizada":
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
                    fecha_fin = COALESCE(?, fecha_fin),
                    fecha_vencimiento = COALESCE(?, fecha_vencimiento),
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    final_status,
                    fecha_fin,
                    expiry_date,
                    datetime.now().isoformat(timespec="seconds"),
                    int(selected_id),
                ),
            )

            add_event(
                int(selected_id),
                "Producción registrada",
                f"{qty} {row['unidad']} · Estado: {final_status}",
            )

            st.success(
                "Producción registrada correctamente."
            )
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

        selected_order = st.selectbox(
            "Seleccionar orden",
            options=view["id"].tolist(),
            format_func=lambda oid: (
                f"{view.loc[view['id'] == oid, 'lote_codigo'].iloc[0]} · "
                f"{view.loc[view['id'] == oid, 'producto'].iloc[0]}"
            ),
        )

        if selected_order:

            detail = view[
                view["id"] == selected_order
            ].iloc[0]

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
                    detail["cantidad_producida"],
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

                people_admin = get_people(
                    active_only=True
                )

                current_resp_email = str(
                    detail.get(
                        "responsable_email"
                    ) or ""
                )

                current_person_match = people_admin[
                    people_admin[
                        "email"
                    ].astype(str)
                    == current_resp_email
                ]

                if current_person_match.empty:
                    default_resp_id = int(
                        people_admin.iloc[0][
                            "id"
                        ]
                    )
                else:
                    default_resp_id = int(
                        current_person_match.iloc[0][
                            "id"
                        ]
                    )

                admin_person_ids = people_admin[
                    "id"
                ].astype(int).tolist()

                with st.form(
                    f"admin_edit_lot_{selected_order}"
                ):

                    e1, e2 = st.columns(2)

                    with e1:

                        edit_product = st.text_input(
                            "Producto",
                            value=str(
                                detail["producto"]
                            ),
                        )

                        edit_planned = st.number_input(
                            "Cantidad planificada",
                            min_value=0.01,
                            value=float(
                                detail[
                                    "cantidad_planificada"
                                ]
                            ),
                            step=1.0,
                        )

                        edit_start = st.date_input(
                            "Fecha de inicio",
                            value=(
                                pd.to_datetime(
                                    detail["fecha_inicio"],
                                    errors="coerce",
                                ).date()
                                if pd.notna(
                                    pd.to_datetime(
                                        detail["fecha_inicio"],
                                        errors="coerce",
                                    )
                                )
                                else date.today()
                            ),
                        )

                        current_finish = pd.to_datetime(
                            detail.get("fecha_fin"),
                            errors="coerce",
                        )

                        has_finish = st.checkbox(
                            "Tiene fecha de finalización",
                            value=pd.notna(
                                current_finish
                            ),
                        )

                        edit_finish = st.date_input(
                            "Fecha de finalización",
                            value=(
                                current_finish.date()
                                if pd.notna(
                                    current_finish
                                )
                                else date.today()
                            ),
                            disabled=not has_finish,
                        )

                    with e2:

                        current_shelf = int(
                            detail.get(
                                "vida_util_meses"
                            )
                            if pd.notna(
                                detail.get(
                                    "vida_util_meses"
                                )
                            )
                            else DEFAULT_SHELF_LIFE_MONTHS
                        )

                        edit_shelf = st.number_input(
                            "Vida útil [meses]",
                            min_value=1,
                            max_value=120,
                            value=current_shelf,
                            step=1,
                        )

                        edit_resp_id = st.selectbox(
                            "Responsable",
                            options=admin_person_ids,
                            index=admin_person_ids.index(
                                default_resp_id
                            ),
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
                                ESTADOS.index(
                                    detail["estado"]
                                )
                                if detail["estado"]
                                in ESTADOS
                                else 0
                            ),
                        )

                        edit_priority = st.selectbox(
                            "Prioridad",
                            PRIORIDADES,
                            index=(
                                PRIORIDADES.index(
                                    detail["prioridad"]
                                )
                                if detail["prioridad"]
                                in PRIORIDADES
                                else 0
                            ),
                        )

                    edit_obs = st.text_area(
                        "Observaciones",
                        value=str(
                            detail.get(
                                "observaciones"
                            )
                            or ""
                        ),
                    )

                    save_edit = st.form_submit_button(
                        "Guardar modificaciones",
                        type="primary",
                        use_container_width=True,
                    )

                if save_edit:

                    selected_resp = people_admin[
                        people_admin[
                            "id"
                        ]
                        == edit_resp_id
                    ].iloc[0]

                    final_date_value = (
                        edit_finish
                        if has_finish
                        else None
                    )

                    expiry_value = (
                        months_after(
                            final_date_value,
                            int(edit_shelf),
                        )
                        if final_date_value
                        else None
                    )

                    execute(
                        """
                        UPDATE ordenes
                        SET producto = ?,
                            cantidad_planificada = ?,
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
                            edit_product.strip(),
                            float(edit_planned),
                            edit_start.isoformat(),
                            (
                                final_date_value.isoformat()
                                if final_date_value
                                else None
                            ),
                            (
                                expiry_value.isoformat()
                                if expiry_value
                                else None
                            ),
                            int(edit_shelf),
                            str(
                                selected_resp["nombre"]
                            ),
                            str(
                                selected_resp["email"]
                            ),
                            edit_status,
                            edit_priority,
                            edit_obs.strip(),
                            datetime.now().isoformat(
                                timespec="seconds"
                            ),
                            int(selected_order),
                        ),
                    )

                    add_event(
                        int(selected_order),
                        "Lote modificado por administrador",
                        (
                            f"Usuario: "
                            f"{st.session_state.get('current_user', 'Administrador')}"
                        ),
                    )

                    st.success(
                        "Lote actualizado correctamente."
                    )
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
# PIE
# ============================================================

st.divider()
st.caption(
    f"SEV | Control de Producción · {APP_VERSION} · "
    "Órdenes internas, lotes, vencimientos, correos y trazabilidad de producción"
)
