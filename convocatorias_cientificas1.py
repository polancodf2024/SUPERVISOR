import streamlit as st
import requests
import pandas as pd
import time
from datetime import datetime, timedelta
import json
import csv
import os
from pathlib import Path
from typing import List, Dict, Optional
import sys
import paramiko
from io import StringIO
import smtplib
import ssl
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.application import MIMEApplication

# ==================== CONFIGURACIÓN DE LA PÁGINA ====================
st.set_page_config(
    page_title="Buscador de Convocatorias Científicas",
    page_icon="🔬",
    layout="wide",
    initial_sidebar_state="expanded"
)

# ==================== VERIFICACIÓN DE DEPENDENCIAS ====================
try:
    from bs4 import BeautifulSoup
    BEAUTIFULSOUP_AVAILABLE = True
except ImportError:
    BEAUTIFULSOUP_AVAILABLE = False

try:
    import feedparser
    FEEDPARSER_AVAILABLE = True
except ImportError:
    FEEDPARSER_AVAILABLE = False

# ==================== CONFIGURACIÓN DE STREAMLIT SECRETS ====================
def cargar_configuracion():
    """Carga la configuración desde secrets.toml con manejo robusto de errores"""
    config = {
        'SMTP_SERVER': None,
        'SMTP_PORT': None,
        'EMAIL_USER': None,
        'EMAIL_PASSWORD': None,
        'NOTIFICATION_EMAIL': None,
        'REMOTE_HOST': None,
        'REMOTE_USER': None,
        'REMOTE_PASSWORD': None,
        'REMOTE_PORT': None,
        'REMOTE_DIR': None,
        'REMOTE_FILE': None,
        'CONFIG_CARGADA': False
    }
    
    try:
        # Intentar cargar desde secrets.toml - SOLO usar valores del secrets.toml, sin defaults
        config['SMTP_SERVER'] = st.secrets.get("smtp_server")
        config['SMTP_PORT'] = int(st.secrets.get("smtp_port")) if st.secrets.get("smtp_port") else None
        config['EMAIL_USER'] = st.secrets.get("email_user")
        config['EMAIL_PASSWORD'] = st.secrets.get("email_password", "").replace(" ", "")  # Limpiar espacios
        config['NOTIFICATION_EMAIL'] = st.secrets.get("notification_email")
        
        # Configuración remota - SOLO usar valores del secrets.toml
        config['REMOTE_HOST'] = st.secrets.get("remote_host")
        config['REMOTE_USER'] = st.secrets.get("remote_user")
        config['REMOTE_PASSWORD'] = st.secrets.get("remote_password")
        config['REMOTE_PORT'] = int(st.secrets.get("remote_port")) if st.secrets.get("remote_port") else None
        config['REMOTE_DIR'] = st.secrets.get("remote_dir")
        config['REMOTE_FILE'] = st.secrets.get("remote_file")
        
        # Verificar que los datos esenciales estén presentes
        if (config['EMAIL_USER'] and config['EMAIL_PASSWORD'] and config['SMTP_SERVER'] and 
            config['REMOTE_HOST'] and config['REMOTE_USER'] and config['REMOTE_PASSWORD'] and 
            config['REMOTE_PORT'] and config['REMOTE_DIR'] and config['REMOTE_FILE']):
            config['CONFIG_CARGADA'] = True
            
    except Exception as e:
        st.error(f"Error al cargar configuración: {e}")
        config['CONFIG_CARGADA'] = False
    
    return config

# Cargar configuración
CONFIG = cargar_configuracion()

# Asignar variables globales
SMTP_SERVER = CONFIG['SMTP_SERVER']
SMTP_PORT = CONFIG['SMTP_PORT']
EMAIL_USER = CONFIG['EMAIL_USER']
EMAIL_PASSWORD = CONFIG['EMAIL_PASSWORD']
NOTIFICATION_EMAIL = CONFIG['NOTIFICATION_EMAIL']

# Variables del servidor remoto - SOLO del secrets.toml
REMOTE_HOST = CONFIG['REMOTE_HOST']
REMOTE_USER = CONFIG['REMOTE_USER']
REMOTE_PASSWORD = CONFIG['REMOTE_PASSWORD']
REMOTE_PORT = CONFIG['REMOTE_PORT']  # Debe ser 3792 según tu secrets.toml
REMOTE_DIR = CONFIG['REMOTE_DIR']
REMOTE_FILE = CONFIG['REMOTE_FILE']
CONFIG_CARGADA = CONFIG['CONFIG_CARGADA']

# ==================== CONFIGURACIÓN DE ARCHIVOS LOCALES ====================
DATA_DIR = Path("data")
DATA_DIR.mkdir(exist_ok=True)
CONVOCATORIAS_FILE = DATA_DIR / "convocatorias.json"
LOG_FILE = DATA_DIR / "envios_log.csv"

# ==================== CONFIGURACIÓN DE DELAYS Y CONTROL DE ENVÍO ====================
PAUSA_ENTRE_CORREOS = 2.0
PAUSA_ENTRE_GRUPOS = 10
GRUPO_SIZE = 5
TIMEOUT_SECONDS = 30

# ==================== FUNCIONES DE CONEXIÓN REMOTA ====================
def conectar_servidor_remoto():
    """Establece conexión SSH con el servidor remoto usando el puerto específico del secrets.toml"""
    if not all([REMOTE_HOST, REMOTE_USER, REMOTE_PASSWORD, REMOTE_PORT]):
        st.error("❌ Configuración remota incompleta en secrets.toml")
        return None
        
    try:
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        ssh.connect(
            hostname=REMOTE_HOST,
            port=REMOTE_PORT,  # Usar el puerto específico del secrets.toml (3792)
            username=REMOTE_USER,
            password=REMOTE_PASSWORD,
            timeout=10,
            allow_agent=False,
            look_for_keys=False,
            compress=True
        )
        return ssh
    except paramiko.AuthenticationException:
        st.error(f"❌ Error de autenticación en {REMOTE_HOST}:{REMOTE_PORT}. Verifica usuario/contraseña.")
        return None
    except paramiko.SSHException as e:
        st.error(f"❌ Error de conexión SSH a {REMOTE_HOST}:{REMOTE_PORT}: {e}")
        return None
    except Exception as e:
        st.error(f"❌ Error al conectar a {REMOTE_HOST}:{REMOTE_PORT}: {e}")
        return None

def leer_archivo_remoto_directo():
    """Lee el archivo CSV directamente desde el servidor remoto"""
    if not all([REMOTE_HOST, REMOTE_USER, REMOTE_PASSWORD, REMOTE_PORT, REMOTE_DIR, REMOTE_FILE]):
        st.warning("⚠️ Configuración remota incompleta en secrets.toml")
        return []
        
    ssh = None
    sftp = None
    try:
        ssh = conectar_servidor_remoto()
        if ssh is None:
            return []
        
        sftp = ssh.open_sftp()
        remote_path = f"{REMOTE_DIR}/{REMOTE_FILE}"
        
        # Verificar si el archivo remoto existe
        try:
            sftp.stat(remote_path)
        except FileNotFoundError:
            st.warning(f"⚠️ Archivo no encontrado en {REMOTE_HOST}:{REMOTE_PORT}: {remote_path}")
            return []
        
        # Leer contenido del archivo remoto
        with sftp.open(remote_path, 'r') as remote_file:
            contenido = remote_file.read().decode('utf-8-sig')
        
        # Procesar CSV
        registros = []
        reader = csv.DictReader(StringIO(contenido))
        for row in reader:
            registro_normalizado = {
                "Fecha": row.get("Fecha", "").strip(),
                "Nombre completo": row.get("Nombre completo", "").strip(),
                "Correo electronico": row.get("Correo electronico", "").strip().lower(),
                "Numero economico": row.get("Numero economico", "").strip(),
                "Estado": row.get("Estado", "").strip().capitalize(),
                "Especialidad": row.get("Especialidad", "").strip()
            }
            registros.append(registro_normalizado)
        
        return registros
        
    except Exception as e:
        st.error(f"❌ Error al leer archivo remoto en {REMOTE_HOST}:{REMOTE_PORT}: {e}")
        return []
    finally:
        if sftp:
            try:
                sftp.close()
            except:
                pass
        if ssh:
            try:
                ssh.close()
            except:
                pass

def obtener_interesados_activos():
    """Obtiene solo los interesados con estado Activo"""
    try:
        interesados = leer_archivo_remoto_directo()
        if not interesados:
            return []
        
        activos = [i for i in interesados if i.get("Estado", "").lower() == "activo"]
        validos = []
        for i in activos:
            email = i.get("Correo electronico", "")
            if email and '@' in email and len(email) > 5:
                validos.append(i)
        return validos
    except Exception as e:
        st.error(f"❌ Error al obtener interesados: {e}")
        return []

def verificar_conexion_remota():
    """Verifica si hay conexión con el servidor remoto"""
    if not all([REMOTE_HOST, REMOTE_USER, REMOTE_PASSWORD, REMOTE_PORT]):
        return False
        
    ssh = None
    try:
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        ssh.connect(
            hostname=REMOTE_HOST,
            port=REMOTE_PORT,
            username=REMOTE_USER,
            password=REMOTE_PASSWORD,
            timeout=5,
            allow_agent=False,
            look_for_keys=False
        )
        return True
    except:
        return False
    finally:
        if ssh:
            try:
                ssh.close()
            except:
                pass

# ==================== FUNCIONES DE ENVÍO DE CORREOS ====================
def probar_conexion_smtp():
    """Prueba la conexión SMTP antes de enviar correos"""
    try:
        if not CONFIG_CARGADA:
            return False, "❌ Configuración SMTP no disponible en secrets.toml"
        
        context = ssl.create_default_context()
        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT, timeout=10) as server:
            server.starttls(context=context)
            server.login(EMAIL_USER, EMAIL_PASSWORD)
        return True, "✅ Conexión SMTP exitosa! Correos listos para enviar."
    except smtplib.SMTPAuthenticationError:
        return False, "❌ Error de autenticación. Verifica tu contraseña de aplicación en secrets.toml"
    except Exception as e:
        return False, f"❌ Error SMTP: {str(e)}"

def enviar_correo_real(destinatario: str, asunto: str, mensaje: str, 
                      nombre_destinatario: str = "") -> bool:
    """Envía un correo real usando SMTP"""
    try:
        if not CONFIG_CARGADA:
            return False
        
        msg = MIMEMultipart()
        msg['From'] = EMAIL_USER
        msg['To'] = destinatario
        msg['Subject'] = asunto
        msg['Reply-To'] = EMAIL_USER
        
        saludo = f"Estimado/a {nombre_destinatario},\n\n" if nombre_destinatario else "Estimado/a investigador/a,\n\n"
        cuerpo_completo = saludo + mensaje
        
        cuerpo_completo += f"""

---
📧 **Sistema Automatizado de Convocatorias Científicas**
🕒 Enviado: {datetime.now().strftime('%d/%m/%Y %H:%M')}
🔬 INCICh - Instituto Nacional de Cardiología
📧 {EMAIL_USER}

*Este es un mensaje automático, por favor no responder directamente.*
"""
        
        msg.attach(MIMEText(cuerpo_completo, 'plain', 'utf-8'))
        
        context = ssl.create_default_context()
        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT, timeout=TIMEOUT_SECONDS) as server:
            server.starttls(context=context)
            server.login(EMAIL_USER, EMAIL_PASSWORD)
            server.send_message(msg)
        
        return True
    except:
        return False

# ==================== CLASE BUSCADOR DE CONVOCATORIAS ====================
class BuscadorConvocatorias:
    def __init__(self):
        self.user_agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        self.headers = {"User-Agent": self.user_agent}
        self.timeout = 15
    
    def buscar_conacyt_secihti(self) -> List[Dict]:
        """Busca convocatorias en SECIHTI México"""
        convocatorias = []
        
        # Convocatoria principal de Ciencia y Humanidades
        convocatorias.append({
            'id': 'SECIHTI-2026-1',
            'titulo': 'Convocatorias Ciencia y Humanidades 2026',
            'entidad': 'SECIHTI México',
            'enlace': 'https://secihti.mx/convocatoria_categoria/ciencias-y-humanidades/',
            'fecha': datetime.now().strftime("%Y-%m-%d"),
            'plazo': 'Consultar en enlace oficial',
            'area': 'Ciencia y Tecnología',
            'pais': 'México'
        })
        
        # Intentar obtener más convocatorias
        if BEAUTIFULSOUP_AVAILABLE:
            try:
                url = "https://secihti.mx/convocatorias/"
                response = requests.get(url, headers=self.headers, timeout=self.timeout)
                
                if response.status_code == 200:
                    soup = BeautifulSoup(response.content, 'html.parser')
                    
                    for i, enlace in enumerate(soup.find_all('a', href=True)):
                        texto = enlace.get_text(strip=True)
                        href = enlace['href']
                        
                        if texto and ('convocatoria' in texto.lower() or 'beca' in texto.lower()) and len(texto) > 15:
                            if len(convocatorias) < 5:
                                convocatorias.append({
                                    'id': f"SECIHTI-{i+2}",
                                    'titulo': texto[:150],
                                    'entidad': 'SECIHTI México',
                                    'enlace': href if href.startswith('http') else f"https://secihti.mx{href}",
                                    'fecha': datetime.now().strftime("%Y-%m-%d"),
                                    'plazo': 'Consultar enlace',
                                    'area': 'Ciencia y Tecnología',
                                    'pais': 'México'
                                })
            except:
                pass
        
        return convocatorias[:5]
    
    def buscar_todas(self) -> List[Dict]:
        """Busca en todas las fuentes disponibles"""
        return self.buscar_conacyt_secihti()
    
    def guardar_convocatorias(self, convocatorias: List[Dict]):
        """Guarda las convocatorias en un archivo JSON"""
        try:
            with open(CONVOCATORIAS_FILE, 'w', encoding='utf-8') as f:
                json.dump(convocatorias, f, ensure_ascii=False, indent=2)
        except:
            pass
    
    def cargar_convocatorias(self) -> List[Dict]:
        """Carga las convocatorias desde el archivo JSON"""
        try:
            if CONVOCATORIAS_FILE.exists():
                with open(CONVOCATORIAS_FILE, 'r', encoding='utf-8') as f:
                    return json.load(f)
            return []
        except:
            return []

# ==================== INTERFAZ PRINCIPAL ====================
def main():
    """Función principal de la aplicación"""
    
    st.title("🔬 Buscador de Convocatorias Científicas")
    st.markdown("---")
    
    # Mostrar estado de configuración
    if CONFIG_CARGADA:
        st.success(f"""
        ✅ **Sistema configurado correctamente**
        - 📧 SMTP: {EMAIL_USER}
        - 🌐 Servidor remoto: {REMOTE_HOST}:{REMOTE_PORT}
        - 📁 Archivo: {REMOTE_DIR}/{REMOTE_FILE}
        """)
        
        # Botón para probar conexión SMTP
        with st.expander("📧 Probar conexión de correo"):
            if st.button("🔌 Probar conexión SMTP", key="test_smtp"):
                with st.spinner("Probando conexión..."):
                    exito, mensaje = probar_conexion_smtp()
                    if exito:
                        st.success(mensaje)
                    else:
                        st.error(mensaje)
        
        # Botón para probar conexión remota
        with st.expander("🌐 Probar conexión al servidor remoto"):
            if st.button("🔌 Probar conexión SFTP", key="test_sftp"):
                with st.spinner(f"Conectando a {REMOTE_HOST}:{REMOTE_PORT}..."):
                    if verificar_conexion_remota():
                        st.success(f"✅ Conexión exitosa a {REMOTE_HOST}:{REMOTE_PORT}")
                    else:
                        st.error(f"❌ No se pudo conectar a {REMOTE_HOST}:{REMOTE_PORT}")
    else:
        st.error("""
        ❌ **Configuración incompleta en secrets.toml**
        
        El archivo `.streamlit/secrets.toml` debe contener TODOS estos campos:
        ```toml
        smtp_server = "smtp.gmail.com"
        smtp_port = 587
        email_user = "cardiologiaproyectos@gmail.com"
        email_password = "vriqjsidzdhifzsu"
        notification_email = "polanco@unam.mx"
        
        remote_host = "187.217.52.137"
        remote_user = "POLANCO6"
        remote_password = "tt6plco6"
        remote_port = 3792
        remote_dir = "/home/POLANCO6"
        remote_file = "registro_interesados.csv"
        ```
        """)
        st.stop()
    
    # Sidebar
    with st.sidebar:
        st.header("⚙️ Configuración")
        
        st.markdown("---")
        st.subheader("📊 Estado del Sistema")
        
        st.success(f"✅ SMTP: {EMAIL_USER[:15]}...")
        
        conectado = verificar_conexion_remota()
        if conectado:
            st.success(f"✅ SFTP: Conectado a {REMOTE_HOST}:{REMOTE_PORT}")
        else:
            st.error(f"❌ SFTP: Desconectado de {REMOTE_HOST}:{REMOTE_PORT}")
        
        st.markdown("---")
        st.subheader("👥 Interesados Remotos")
        
        if st.button("🔄 Cargar interesados activos", use_container_width=True):
            with st.spinner(f"Cargando desde {REMOTE_HOST}:{REMOTE_PORT}..."):
                interesados = obtener_interesados_activos()
                if interesados:
                    st.success(f"✅ {len(interesados)} interesados cargados")
                    st.session_state['interesados_activos'] = interesados
                else:
                    st.warning("⚠️ No se encontraron interesados activos")
        
        if 'interesados_activos' in st.session_state:
            st.caption(f"📋 {len(st.session_state['interesados_activos'])} registros en memoria")
        
        st.markdown("---")
        st.subheader("🎯 Fuentes de búsqueda")
        
        fuente_conacyt = st.checkbox("SECIHTI México", value=True)
        
        st.markdown("---")
        st.info(f"""
        **📋 Configuración actual:**
        - 🌐 Host: {REMOTE_HOST}
        - 🔌 Puerto: {REMOTE_PORT}
        - 📁 Archivo: {REMOTE_FILE}
        """)
    
    # Tabs principales
    tab1, tab2 = st.tabs(["🔍 Buscar Convocatorias", "📧 Enviar a Interesados"])
    
    with tab1:
        st.header("Búsqueda de Convocatorias")
        
        col1, col2, col3 = st.columns([1, 2, 1])
        with col2:
            buscar_btn = st.button("🔍 BUSCAR CONVOCATORIAS", type="primary", use_container_width=True)
        
        if buscar_btn:
            buscador = BuscadorConvocatorias()
            convocatorias = buscador.buscar_todas()
            
            if convocatorias:
                buscador.guardar_convocatorias(convocatorias)
                st.session_state['ultimas_convocatorias'] = convocatorias
                
                df = pd.DataFrame(convocatorias)
                
                st.subheader(f"📊 Resultados: {len(df)} convocatorias")
                
                st.dataframe(
                    df,
                    column_config={
                        "id": "ID",
                        "titulo": st.column_config.TextColumn("Título", width="large"),
                        "entidad": "Entidad",
                        "enlace": st.column_config.LinkColumn("Enlace"),
                        "fecha": "Fecha",
                        "plazo": "Plazo",
                        "area": "Área",
                        "pais": "País"
                    },
                    hide_index=True,
                    use_container_width=True
                )
                
                csv_data = df.to_csv(index=False).encode('utf-8')
                st.download_button(
                    label="📥 Descargar CSV",
                    data=csv_data,
                    file_name=f"convocatorias_{datetime.now().strftime('%Y%m%d')}.csv",
                    mime="text/csv"
                )
                
                st.success("✅ Convocatorias listas para enviar")
                st.balloons()
            else:
                st.warning("⚠️ No se encontraron convocatorias")
    
    with tab2:
        st.header("📧 Envío de Convocatorias")
        
        buscador = BuscadorConvocatorias()
        
        if 'ultimas_convocatorias' in st.session_state:
            convocatorias = st.session_state['ultimas_convocatorias']
        else:
            convocatorias = buscador.cargar_convocatorias()
        
        if not convocatorias:
            st.info("📌 Busca convocatorias primero en la pestaña anterior")
        else:
            if 'interesados_activos' in st.session_state:
                interesados = st.session_state['interesados_activos']
            else:
                with st.spinner(f"Cargando interesados de {REMOTE_HOST}:{REMOTE_PORT}..."):
                    interesados = obtener_interesados_activos()
                    if interesados:
                        st.session_state['interesados_activos'] = interesados
            
            if not interesados:
                st.warning("⚠️ Carga interesados desde el sidebar primero")
            else:
                col1, col2 = st.columns(2)
                with col1:
                    st.metric("📋 Convocatorias", len(convocatorias))
                with col2:
                    st.metric("👥 Interesados activos", len(interesados))
                
                # Seleccionar convocatoria
                st.subheader("1️⃣ Selecciona convocatoria")
                
                opciones = {c['id']: f"{c['titulo'][:60]}... - {c['entidad']}" for c in convocatorias}
                conv_id = st.selectbox("Convocatorias disponibles:", options=list(opciones.keys()), format_func=lambda x: opciones[x])
                
                conv_seleccionada = next((c for c in convocatorias if c['id'] == conv_id), None)
                
                if conv_seleccionada:
                    with st.container(border=True):
                        st.markdown(f"### {conv_seleccionada['titulo']}")
                        st.write(f"**🏛️ Entidad:** {conv_seleccionada['entidad']}")
                        st.write(f"**🔗 Enlace:** {conv_seleccionada['enlace']}")
                        st.write(f"**📅 Publicación:** {conv_seleccionada['fecha']}")
                        st.write(f"**⏰ Plazo:** {conv_seleccionada['plazo']}")
                    
                    # Seleccionar destinatarios
                    st.subheader("2️⃣ Selecciona destinatarios")
                    
                    seleccionar_todos = st.checkbox("✓ Seleccionar todos", value=False)
                    
                    seleccionados = []
                    cols = st.columns(2)
                    for i, inv in enumerate(interesados):
                        with cols[i % 2]:
                            nombre = inv.get('Nombre completo', 'Sin nombre')
                            email = inv.get('Correo electronico', '')
                            especialidad = inv.get('Especialidad', 'No especificada')
                            
                            selec = st.checkbox(
                                f"**{nombre}**\n📧 {email}\n🏷️ {especialidad}",
                                value=seleccionar_todos,
                                key=f"inv_{i}"
                            )
                            if selec:
                                seleccionados.append({'nombre': nombre, 'email': email})
                    
                    st.info(f"📌 **{len(seleccionados)}** destinatarios seleccionados")
                    
                    # Configurar y enviar
                    if seleccionados:
                        st.subheader("3️⃣ Enviar correos")
                        
                        asunto = st.text_input(
                            "**Asunto del correo:**",
                            value=f"📢 Convocatoria: {conv_seleccionada['titulo'][:80]}..."
                        )
                        
                        mensaje_default = f"""
Te informamos sobre la siguiente convocatoria de financiamiento:

🎯 **CONVOCATORIA:** {conv_seleccionada['titulo']}

📋 **DETALLES DE LA CONVOCATORIA:**
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🏛️ **Entidad convocante:** {conv_seleccionada['entidad']}
🔬 **Área de investigación:** {conv_seleccionada['area']}
🌍 **País/Región:** {conv_seleccionada.get('pais', 'México')}
📅 **Fecha de publicación:** {conv_seleccionada['fecha']}
⏰ **Plazo límite:** {conv_seleccionada['plazo']}
🔗 **Enlace oficial:** {conv_seleccionada['enlace']}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

📌 **RECOMENDACIONES:**
1. Revisa los requisitos en el enlace oficial
2. Prepara la documentación necesaria
3. Verifica las fechas límite

---
🔬 **Instituto Nacional de Cardiología - INCICh**
📧 Sistema de Convocatorias Científicas
"""
                        
                        mensaje = st.text_area("**Mensaje del correo:**", value=mensaje_default, height=300)
                        
                        if st.button("📤 ENVIAR CORREOS", type="primary", use_container_width=True):
                            if not CONFIG_CARGADA:
                                st.error("❌ Configuración SMTP incompleta")
                            else:
                                progress_bar = st.progress(0)
                                status_text = st.empty()
                                
                                exitosos = 0
                                total = len(seleccionados)
                                
                                for i, inv in enumerate(seleccionados):
                                    status_text.text(f"📨 Enviando {i+1} de {total}: {inv['email']}")
                                    
                                    if enviar_correo_real(inv['email'], asunto, mensaje, inv['nombre']):
                                        exitosos += 1
                                    
                                    progress_bar.progress((i + 1) / total)
                                    time.sleep(PAUSA_ENTRE_CORREOS)
                                    
                                    if (i + 1) % GRUPO_SIZE == 0 and (i + 1) < total:
                                        status_text.text(f"⏸️ Pausa de {PAUSA_ENTRE_GRUPOS} segundos...")
                                        time.sleep(PAUSA_ENTRE_GRUPOS)
                                
                                progress_bar.empty()
                                status_text.empty()
                                
                                st.success(f"""
                                ### ✅ ¡Envío completado!
                                - 📨 Total: {total}
                                - ✅ Exitosos: {exitosos}
                                - ❌ Fallidos: {total - exitosos}
                                - 📈 Tasa: {(exitosos/total*100):.1f}%
                                """)
                                
                                if exitosos > 0:
                                    st.balloons()
                    else:
                        st.info("👆 **Selecciona al menos un destinatario**")

# ==================== EJECUCIÓN ====================
if __name__ == "__main__":
    main()
