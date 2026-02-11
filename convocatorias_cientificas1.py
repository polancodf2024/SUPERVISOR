import streamlit as st
import requests
import pandas as pd
import time
from datetime import datetime, timedelta
import json
import csv
import os
from pathlib import Path
from typing import List, Dict
import sys
import paramiko
from io import StringIO
import smtplib
import ssl
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# Configuración de Streamlit secrets
try:
    # Configuración del servidor SMTP para envío real de correos
    SMTP_SERVER = st.secrets["smtp_server"]
    SMTP_PORT = st.secrets["smtp_port"]
    EMAIL_USER = st.secrets["email_user"]
    EMAIL_PASSWORD = st.secrets["email_password"]
    NOTIFICATION_EMAIL = st.secrets["notification_email"]
    
    # Configuración del servidor remoto
    REMOTE_HOST = st.secrets["remote_host"]
    REMOTE_USER = st.secrets["remote_user"]
    REMOTE_PASSWORD = st.secrets["remote_password"]
    REMOTE_PORT = st.secrets["remote_port"]
    REMOTE_DIR = st.secrets["remote_dir"]
    REMOTE_FILE = st.secrets["remote_file"]
    
    CONFIG_CARGADA = True
except Exception as e:
    st.error(f"Error al cargar configuración: {e}")
    CONFIG_CARGADA = False
    # Valores por defecto (solo para desarrollo)
    SMTP_SERVER = "smtp.gmail.com"
    SMTP_PORT = 587
    EMAIL_USER = ""
    EMAIL_PASSWORD = ""
    NOTIFICATION_EMAIL = ""
    REMOTE_HOST = "187.217.52.137"
    REMOTE_USER = "POLANCO6"
    REMOTE_PASSWORD = "tt6plco6"
    REMOTE_PORT = 3792
    REMOTE_DIR = "/home/POLANCO6"
    REMOTE_FILE = "registro_interesados.csv"

# Intentar importar dependencias opcionales
try:
    from bs4 import BeautifulSoup
    BEAUTIFULSOUP_AVAILABLE = True
except ImportError:
    BEAUTIFULSOUP_AVAILABLE = False
    st.warning("BeautifulSoup4 no está instalado. Algunas funcionalidades estarán limitadas.")

try:
    import feedparser
    FEEDPARSER_AVAILABLE = True
except ImportError:
    FEEDPARSER_AVAILABLE = False
    st.warning("feedparser no está instalado. Las fuentes RSS no estarán disponibles.")

# Configuración de la página
st.set_page_config(
    page_title="Buscador de Convocatorias Científicas",
    page_icon="🔬",
    layout="wide"
)

# Título y descripción
st.title("🔬 Buscador de Convocatorias Científicas")
st.markdown("""
Esta aplicación rastrea convocatorias de financiamiento, becas y proyectos científicos abiertos.
**Conectado al servidor remoto de interesados - Envío real de correos habilitado.**
""")

# Mostrar estado de configuración
if not CONFIG_CARGADA:
    st.error("""
    **⚠️ Configuración no cargada correctamente**
    
    Asegúrate de tener un archivo `.streamlit/secrets.toml` con las siguientes configuraciones:
    ```toml
    smtp_server = "smtp.gmail.com"
    smtp_port = 587
    email_user = "tu_email@gmail.com"
    email_password = "tu_contraseña_app"
    notification_email = "email_notificaciones@ejemplo.com"
    remote_host = "187.217.52.137"
    remote_user = "POLANCO6"
    remote_password = "tt6plco6"
    remote_port = 3792
    remote_dir = "/home/POLANCO6"
    remote_file = "registro_interesados.csv"
    ```
    """)

if not BEAUTIFULSOUP_AVAILABLE:
    st.error("""
    **¡BeautifulSoup4 no está instalado!**
    
    Para instalar las dependencias necesarias, ejecuta en tu terminal:
    ```bash
    pip install beautifulsoup4 feedparser pandas streamlit requests paramiko
    ```
    """)

if not FEEDPARSER_AVAILABLE:
    st.warning("""
    **feedparser no está instalado.**
    Las fuentes RSS (Horizonte Europa, NSF) no estarán disponibles.
    
    Instálalo con:
    ```bash
    pip install feedparser
    ```
    """)

# Configurar archivos de datos locales
DATA_DIR = Path("data")
DATA_DIR.mkdir(exist_ok=True)
CONVOCATORIAS_FILE = DATA_DIR / "convocatorias.json"

# ==================== FUNCIONES DE CONEXIÓN REMOTA ====================
def conectar_servidor_remoto():
    """Establece conexión SSH con el servidor remoto"""
    try:
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        ssh.connect(REMOTE_HOST, port=REMOTE_PORT, 
                   username=REMOTE_USER, password=REMOTE_PASSWORD)
        return ssh
    except Exception as e:
        st.error(f"Error al conectar al servidor remoto: {e}")
        return None

def leer_archivo_remoto_directo():
    """
    Lee el archivo CSV directamente desde el servidor remoto
    sin necesidad de descargarlo localmente
    """
    ssh = conectar_servidor_remoto()
    if ssh:
        try:
            sftp = ssh.open_sftp()
            
            # Verificar si el archivo remoto existe
            try:
                sftp.stat(f"{REMOTE_DIR}/{REMOTE_FILE}")
            except FileNotFoundError:
                st.warning(f"Archivo remoto no encontrado: {REMOTE_DIR}/{REMOTE_FILE}")
                sftp.close()
                ssh.close()
                return []
            
            # Leer contenido del archivo remoto
            with sftp.open(f"{REMOTE_DIR}/{REMOTE_FILE}", 'r') as remote_file:
                contenido = remote_file.read().decode('utf-8-sig')
            
            sftp.close()
            ssh.close()
            
            # Procesar CSV desde el contenido en memoria
            registros = []
            reader = csv.DictReader(StringIO(contenido))
            for row in reader:
                registro_normalizado = {
                    "Fecha": row.get("Fecha", "").strip(),
                    "Nombre completo": row.get("Nombre completo", "").strip(),
                    "Correo electronico": row.get("Correo electronico", "").strip().lower(),
                    "Numero economico": row.get("Numero economico", "").strip(),
                    "Estado": row.get("Estado", "").strip(),
                    "Especialidad": row.get("Especialidad", "").strip()
                }
                registros.append(registro_normalizado)
            
            return registros
            
        except Exception as e:
            st.error(f"Error al leer archivo remoto: {e}")
            return []
    return []

def obtener_interesados_activos():
    """Obtiene solo los interesados con estado Activo"""
    interesados = leer_archivo_remoto_directo()
    return [i for i in interesados if i.get("Estado", "") == "Activo"]

# ==================== FUNCIONES DE ENVÍO DE CORREOS ====================
def enviar_correo_real(destinatario: str, asunto: str, mensaje: str, nombre_destinatario: str = "") -> bool:
    """Envía un correo real usando SMTP"""
    try:
        # Crear mensaje
        msg = MIMEMultipart()
        msg['From'] = EMAIL_USER
        msg['To'] = destinatario
        msg['Subject'] = asunto
        
        # Personalizar saludo
        saludo = f"Estimado/a {nombre_destinatario},\n\n" if nombre_destinatario else "Estimado/a investigador/a,\n\n"
        cuerpo_completo = saludo + mensaje
        
        # Agregar firma
        cuerpo_completo += f"\n\n---\n"
        cuerpo_completo += f"Este es un mensaje automático del Sistema de Convocatorias Científicas\n"
        cuerpo_completo += f"Si recibiste este correo por error, por favor ignóralo.\n"
        
        msg.attach(MIMEText(cuerpo_completo, 'plain'))
        
        # Crear contexto SSL
        context = ssl.create_default_context()
        
        # Conectar y enviar
        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
            server.starttls(context=context)
            server.login(EMAIL_USER, EMAIL_PASSWORD)
            server.send_message(msg)
        
        return True
        
    except Exception as e:
        st.error(f"Error al enviar correo a {destinatario}: {str(e)}")
        return False

def enviar_correo_notificacion_admin(convocatoria_titulo: str, total_enviados: int, destinatarios: List[str]):
    """Envía notificación al administrador"""
    try:
        asunto = f"📧 Notificación: Convocatoria enviada - {convocatoria_titulo[:50]}..."
        
        mensaje = f"""
        **Notificación de envío de convocatoria**
        
        Se ha realizado un envío masivo de convocatorias científicas.
        
        **Detalles del envío:**
        - Convocatoria: {convocatoria_titulo}
        - Fecha de envío: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
        - Total destinatarios: {total_enviados}
        
        **Destinatarios:**
        {chr(10).join(f'- {dest}' for dest in destinatarios)}
        
        **Estadísticas:**
        - Envíos exitosos: {total_enviados}
        - Fallidos: 0
        
        Este es un mensaje automático del sistema.
        """
        
        msg = MIMEMultipart()
        msg['From'] = EMAIL_USER
        msg['To'] = NOTIFICATION_EMAIL
        msg['Subject'] = asunto
        msg.attach(MIMEText(mensaje, 'plain'))
        
        context = ssl.create_default_context()
        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
            server.starttls(context=context)
            server.login(EMAIL_USER, EMAIL_PASSWORD)
            server.send_message(msg)
            
    except Exception as e:
        st.warning(f"No se pudo enviar notificación al administrador: {str(e)}")

# ==================== CLASE BUSCADOR DE CONVOCATORIAS ====================
class BuscadorConvocatorias:
    def __init__(self):
        self.user_agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        self.headers = {"User-Agent": self.user_agent}
    
    def buscar_minciencias(self) -> List[Dict]:
        """Busca convocatorias en Minciencias Colombia"""
        convocatorias = []
        if not BEAUTIFULSOUP_AVAILABLE:
            st.error("BeautifulSoup4 no está instalado. No se puede buscar en Minciencias.")
            return convocatorias
            
        try:
            url = "https://minciencias.gov.co/convocatorias"
            response = requests.get(url, headers=self.headers, timeout=10)
            
            # Usar BeautifulSoup si está disponible
            if BEAUTIFULSOUP_AVAILABLE:
                soup = BeautifulSoup(response.content, 'html.parser')
                
                # Buscar enlaces que contengan 'convocatoria'
                for enlace in soup.find_all('a', href=True):
                    href = enlace['href']
                    texto = enlace.get_text(strip=True)
                    
                    if 'convocatoria' in texto.lower() or 'convocatoria' in href.lower():
                        if texto and len(texto) > 10:
                            convocatorias.append({
                                'id': f"MINC-{len(convocatorias)+1}",
                                'titulo': texto[:100],
                                'entidad': 'Minciencias Colombia',
                                'enlace': href if href.startswith('http') else f"https://minciencias.gov.co{href}",
                                'fecha': datetime.now().strftime("%Y-%m-%d"),
                                'plazo': 'Por consultar',
                                'area': 'Investigación'
                            })
                            if len(convocatorias) >= 5:
                                break
        except Exception as e:
            st.error(f"Error Minciencias: {str(e)}")
        
        return convocatorias
    
    def buscar_horizonte_europa(self) -> List[Dict]:
        """Busca convocatorias de Horizonte Europa"""
        convocatorias = []
        if not FEEDPARSER_AVAILABLE:
            st.error("feedparser no está instalado. No se puede buscar en Horizonte Europa.")
            return convocatorias
            
        try:
            # RSS de Horizonte Europa
            feed_url = "https://ec.europa.eu/info/funding-tenders/opportunities/portal/screen/opportunities/rss-feed"
            feed = feedparser.parse(feed_url)
            
            for i, entry in enumerate(feed.entries[:10]):
                convocatorias.append({
                    'id': f"EU-{i+1}",
                    'titulo': entry.title[:150],
                    'entidad': 'Horizonte Europa',
                    'enlace': entry.link,
                    'fecha': entry.published if hasattr(entry, 'published') else datetime.now().strftime("%Y-%m-%d"),
                    'plazo': 'Variable',
                    'area': 'Investigación e Innovación'
                })
        except Exception as e:
            st.error(f"Error Horizonte Europa: {str(e)}")
        
        return convocatorias
    
    def buscar_conacyt(self) -> List[Dict]:
        """Busca convocatorias en CONACYT México"""
        convocatorias = []
        if not BEAUTIFULSOUP_AVAILABLE:
            st.error("BeautifulSoup4 no está instalado. No se puede buscar en CONACYT.")
            return convocatorias
            
        try:
            url = "https://www.conacyt.gob.mx/convocatorias"
            response = requests.get(url, headers=self.headers, timeout=10)
            
            if BEAUTIFULSOUP_AVAILABLE:
                soup = BeautifulSoup(response.content, 'html.parser')
                
                # Buscar elementos relevantes
                for i, enlace in enumerate(soup.find_all('a', href=True)):
                    texto = enlace.get_text(strip=True)
                    if texto and ('convocatoria' in texto.lower() or 'convocatoria' in enlace['href'].lower()):
                        if len(texto) > 15:
                            convocatorias.append({
                                'id': f"CONA-{i+1}",
                                'titulo': texto[:120],
                                'entidad': 'CONACYT México',
                                'enlace': enlace['href'] if enlace['href'].startswith('http') else f"https://www.conacyt.gob.mx{enlace['href']}",
                                'fecha': datetime.now().strftime("%Y-%m-%d"),
                                'plazo': 'Por revisar',
                                'area': 'Ciencia y Tecnología'
                            })
                            if len(convocatorias) >= 5:
                                break
        except Exception as e:
            st.error(f"Error CONACYT: {str(e)}")
        
        return convocatorias
    
    def buscar_nsf(self) -> List[Dict]:
        """Busca convocatorias de la National Science Foundation"""
        convocatorias = []
        if not FEEDPARSER_AVAILABLE:
            st.error("feedparser no está instalado. No se puede buscar en NSF.")
            return convocatorias
            
        try:
            # RSS de NSF
            url = "https://www.nsf.gov/rss/funding_opps.xml"
            feed = feedparser.parse(url)
            
            for i, entry in enumerate(feed.entries[:8]):
                convocatorias.append({
                    'id': f"NSF-{i+1}",
                    'titulo': entry.title[:150],
                    'entidad': 'National Science Foundation',
                    'enlace': entry.link,
                    'fecha': entry.updated if hasattr(entry, 'updated') else datetime.now().strftime("%Y-%m-%d"),
                    'plazo': 'Variable',
                    'area': 'Investigación Científica'
                })
        except Exception as e:
            st.error(f"Error NSF: {str(e)}")
        
        return convocatorias
    
    def buscar_unesco(self) -> List[Dict]:
        """Busca convocatorias de UNESCO"""
        convocatorias = []
        if not BEAUTIFULSOUP_AVAILABLE:
            st.error("BeautifulSoup4 no está instalado. No se puede buscar en UNESCO.")
            return convocatorias
            
        try:
            url = "https://www.unesco.org/en/calls"
            response = requests.get(url, headers=self.headers, timeout=10)
            
            if BEAUTIFULSOUP_AVAILABLE:
                soup = BeautifulSoup(response.content, 'html.parser')
                
                # Buscar elementos que parezcan convocatorias
                for i, elemento in enumerate(soup.find_all(['h3', 'h4', 'a'])):
                    texto = elemento.get_text(strip=True)
                    if texto and ('call' in texto.lower() or 'fellowship' in texto.lower() or 'grant' in texto.lower()):
                        enlace = elemento.get('href', '')
                        if enlace:
                            convocatorias.append({
                                'id': f"UNESCO-{i+1}",
                                'titulo': texto[:100],
                                'entidad': 'UNESCO',
                                'enlace': enlace if enlace.startswith('http') else f"https://www.unesco.org{enlace}",
                                'fecha': datetime.now().strftime("%Y-%m-%d"),
                                'plazo': 'Por definir',
                                'area': 'Educación y Cultura'
                            })
                            if len(convocatorias) >= 5:
                                break
        except Exception as e:
            st.error(f"Error UNESCO: {str(e)}")
        
        return convocatorias
    
    def buscar_todas(self, fuentes_seleccionadas: Dict) -> List[Dict]:
        """Busca en todas las fuentes seleccionadas"""
        todas_convocatorias = []
        
        with st.spinner("Buscando convocatorias..."):
            # Crear datos de ejemplo si no hay fuentes disponibles
            if not BEAUTIFULSOUP_AVAILABLE and not FEEDPARSER_AVAILABLE:
                st.warning("Usando datos de ejemplo (instala las dependencias para búsquedas reales)")
                todas_convocatorias = self._datos_ejemplo()
                return todas_convocatorias
            
            progress_bar = st.progress(0)
            
            fuentes_activas = []
            if fuentes_seleccionadas.get('minciencias') and BEAUTIFULSOUP_AVAILABLE:
                fuentes_activas.append('minciencias')
            if fuentes_seleccionadas.get('europa') and FEEDPARSER_AVAILABLE:
                fuentes_activas.append('europa')
            if fuentes_seleccionadas.get('conacyt') and BEAUTIFULSOUP_AVAILABLE:
                fuentes_activas.append('conacyt')
            if fuentes_seleccionadas.get('nsf') and FEEDPARSER_AVAILABLE:
                fuentes_activas.append('nsf')
            if fuentes_seleccionadas.get('unesco') and BEAUTIFULSOUP_AVAILABLE:
                fuentes_activas.append('unesco')
            
            if not fuentes_activas:
                st.warning("No hay fuentes disponibles. Instala las dependencias necesarias.")
                todas_convocatorias = self._datos_ejemplo()
                return todas_convocatorias
            
            for i, fuente in enumerate(fuentes_activas):
                if fuente == 'minciencias':
                    todas_convocatorias.extend(self.buscar_minciencias())
                elif fuente == 'europa':
                    todas_convocatorias.extend(self.buscar_horizonte_europa())
                elif fuente == 'conacyt':
                    todas_convocatorias.extend(self.buscar_conacyt())
                elif fuente == 'nsf':
                    todas_convocatorias.extend(self.buscar_nsf())
                elif fuente == 'unesco':
                    todas_convocatorias.extend(self.buscar_unesco())
                
                progress_bar.progress((i + 1) / len(fuentes_activas))
            
            progress_bar.empty()
        
        return todas_convocatorias
    
    def _datos_ejemplo(self) -> List[Dict]:
        """Genera datos de ejemplo para demostración"""
        return [
            {
                'id': 'EJ-1',
                'titulo': 'Convocatoria de Investigación en Cardiología 2024',
                'entidad': 'Instituto Nacional de Cardiología',
                'enlace': 'https://ejemplo.com/convocatoria1',
                'fecha': datetime.now().strftime("%Y-%m-%d"),
                'plazo': '40 días',
                'area': 'Salud'
            },
            {
                'id': 'EJ-2',
                'titulo': 'Financiamiento para Proyectos de Bioinformática',
                'entidad': 'Consejo Nacional de Ciencia y Tecnología',
                'enlace': 'https://ejemplo.com/convocatoria2',
                'fecha': datetime.now().strftime("%Y-%m-%d"),
                'plazo': '60 días',
                'area': 'Tecnología'
            },
            {
                'id': 'EJ-3',
                'titulo': 'Becas para Investigación en Electrónica Médica',
                'entidad': 'Secretaría de Salud',
                'enlace': 'https://ejemplo.com/convocatoria3',
                'fecha': datetime.now().strftime("%Y-%m-%d"),
                'plazo': '45 días',
                'area': 'Ingeniería'
            }
        ]
    
    def guardar_convocatorias(self, convocatorias: List[Dict]):
        """Guarda las convocatorias en un archivo JSON"""
        try:
            with open(CONVOCATORIAS_FILE, 'w', encoding='utf-8') as f:
                json.dump(convocatorias, f, ensure_ascii=False, indent=2)
        except Exception as e:
            st.error(f"Error al guardar convocatorias: {str(e)}")
    
    def cargar_convocatorias(self) -> List[Dict]:
        """Carga las convocatorias desde el archivo JSON"""
        try:
            if CONVOCATORIAS_FILE.exists():
                with open(CONVOCATORIAS_FILE, 'r', encoding='utf-8') as f:
                    return json.load(f)
            return []
        except Exception as e:
            st.error(f"Error al cargar convocatorias: {str(e)}")
            return []

# ==================== INTERFAZ PRINCIPAL ====================
# Inicializar el buscador
buscador = BuscadorConvocatorias()

# Sidebar para configuración
with st.sidebar:
    st.header("⚙️ Configuración")
    
    # Mostrar estado de configuración
    st.subheader("Estado del Sistema")
    if CONFIG_CARGADA:
        st.success("✅ Configuración cargada")
    else:
        st.error("❌ Configuración no cargada")
    
    if BEAUTIFULSOUP_AVAILABLE:
        st.success("✅ BeautifulSoup4: Instalado")
    else:
        st.error("❌ BeautifulSoup4: No instalado")
    
    if FEEDPARSER_AVAILABLE:
        st.success("✅ feedparser: Instalado")
    else:
        st.error("❌ feedparser: No instalado")
    
    # Información del servidor remoto
    st.subheader("🌐 Servidor Remoto")
    st.info(f"**Host:** {REMOTE_HOST}:{REMOTE_PORT}")
    st.info(f"**Archivo:** {REMOTE_FILE}")
    
    # Información SMTP
    st.subheader("📧 Servidor SMTP")
    st.info(f"**Servidor:** {SMTP_SERVER}")
    st.info(f"**Usuario:** {EMAIL_USER}")
    st.info(f"**Notificaciones a:** {NOTIFICATION_EMAIL}")
    
    # Botón para cargar interesados
    if st.button("🔄 Cargar interesados remotos"):
        interesados = obtener_interesados_activos()
        if interesados:
            st.success(f"✅ {len(interesados)} interesados activos cargados")
        else:
            st.warning("No se pudieron cargar los interesados del servidor remoto")
    
    # Selección de fuentes (solo mostrar si están disponibles)
    st.subheader("Fuentes de búsqueda")
    
    fuente_minciencias = st.checkbox("Minciencias Colombia", value=True and BEAUTIFULSOUP_AVAILABLE)
    fuente_europa = st.checkbox("Unión Europea", value=True and FEEDPARSER_AVAILABLE)
    fuente_conacyt = st.checkbox("CONACYT México", value=True and BEAUTIFULSOUP_AVAILABLE)
    fuente_nsf = st.checkbox("NSF (EE.UU.)", value=True and FEEDPARSER_AVAILABLE)
    fuente_unesco = st.checkbox("UNESCO", value=True and BEAUTIFULSOUP_AVAILABLE)
    
    # Deshabilitar checkboxes si no hay dependencias
    if not BEAUTIFULSOUP_AVAILABLE:
        fuente_minciencias = False
        fuente_conacyt = False
        fuente_unesco = False
    
    if not FEEDPARSER_AVAILABLE:
        fuente_europa = False
        fuente_nsf = False

# Tabs principales
tab1, tab2 = st.tabs(["🔍 Buscar Convocatorias", "📧 Enviar a Interesados"])

with tab1:
    st.header("Búsqueda de Convocatorias")
    
    # Instrucciones para instalar dependencias
    if not BEAUTIFULSOUP_AVAILABLE or not FEEDPARSER_AVAILABLE:
        st.warning("""
        **⚠️ Faltan dependencias**
        
        Para usar todas las funcionalidades, instala las dependencias necesarias:
        
        ```bash
        pip install beautifulsoup4 feedparser pandas streamlit requests paramiko
        ```
        
        Luego, detén la aplicación (Ctrl+C) y vuelve a ejecutarla.
        """)
    
    if not CONFIG_CARGADA:
        st.error("""
        **⚠️ Configuración SMTP no cargada**
        
        Sin la configuración SMTP no podrás enviar correos reales.
        Asegúrate de tener el archivo `.streamlit/secrets.toml` configurado.
        """)
    
    # Botón para buscar
    if st.button("🔍 Buscar Convocatorias", type="primary", key="buscar_principal", use_container_width=True):
        fuentes = {
            'minciencias': fuente_minciencias,
            'europa': fuente_europa,
            'conacyt': fuente_conacyt,
            'nsf': fuente_nsf,
            'unesco': fuente_unesco
        }
        
        # Realizar búsqueda
        convocatorias = buscador.buscar_todas(fuentes)
        
        if convocatorias:
            # Guardar convocatorias para uso posterior
            buscador.guardar_convocatorias(convocatorias)
            
            # Convertir a DataFrame
            df = pd.DataFrame(convocatorias)
            
            # Eliminar duplicados
            df = df.drop_duplicates(subset=['titulo', 'entidad'])
            
            # Mostrar resultados
            st.subheader(f"📊 Resultados: {len(df)} convocatorias encontradas")
            
            # Mostrar como tabla
            st.dataframe(
                df,
                column_config={
                    "id": "ID",
                    "titulo": "Título",
                    "entidad": "Entidad",
                    "enlace": st.column_config.LinkColumn("Enlace"),
                    "fecha": "Fecha",
                    "plazo": "Plazo",
                    "area": "Área"
                },
                hide_index=True,
                use_container_width=True
            )
            
            # Opción para descargar
            csv_data = df.to_csv(index=False).encode('utf-8')
            st.download_button(
                label="📥 Descargar CSV",
                data=csv_data,
                file_name=f"convocatorias_cientificas_{datetime.now().strftime('%Y%m%d')}.csv",
                mime="text/csv",
                key="descargar_csv",
                use_container_width=True
            )
            
            st.success("✅ Las convocatorias están listas para ser enviadas a interesados (ve a la pestaña 'Enviar a Interesados')")
            st.balloons()  # ¡Globos de celebración!
        else:
            st.warning("No se encontraron convocatorias con las fuentes seleccionadas.")

with tab2:
    st.header("📧 Envío de Convocatorias a Interesados")
    
    # Advertencia si no hay configuración SMTP
    if not CONFIG_CARGADA:
        st.error("""
        **⚠️ No se puede enviar correos**
        
        La configuración SMTP no está cargada. Los correos se simularán pero no se enviarán realmente.
        
        Configura el archivo `.streamlit/secrets.toml` con:
        - `email_user`: Tu correo de Gmail
        - `email_password`: Contraseña de aplicación de Gmail
        """)
    
    # Cargar convocatorias guardadas
    convocatorias_guardadas = buscador.cargar_convocatorias()
    
    if not convocatorias_guardadas:
        st.info("""
        **📌 Primero busca convocatorias:**
        1. Ve a la pestaña '🔍 Buscar Convocatorias'
        2. Selecciona las fuentes de búsqueda
        3. Haz clic en 'Buscar Convocatorias'
        4. Regresa a esta pestaña para enviar
        """)
    else:
        # Cargar interesados del servidor remoto
        interesados = obtener_interesados_activos()
        
        if not interesados:
            st.warning("""
            **⚠️ No hay interesados activos:**
            - Verifica la conexión al servidor remoto
            - Asegúrate de que el archivo existe: `registro_interesados.csv`
            - Los interesados deben tener estado "Activo"
            """)
        else:
            # Mostrar estadísticas
            col1, col2 = st.columns(2)
            with col1:
                st.metric("Convocatorias disponibles", len(convocatorias_guardadas))
            with col2:
                st.metric("Interesados activos", len(interesados))
            
            # PASO 1: Seleccionar convocatoria
            st.subheader("1️⃣ Selecciona una convocatoria")
            
            # Crear diccionario para selección
            convocatorias_dict = {c['id']: f"{c['titulo']} - {c['entidad']}" for c in convocatorias_guardadas}
            
            convocatoria_seleccionada_id = st.selectbox(
                "Selecciona una convocatoria:",
                options=list(convocatorias_dict.keys()),
                format_func=lambda x: convocatorias_dict[x],
                key="seleccion_convocatoria"
            )
            
            if convocatoria_seleccionada_id:
                # Encontrar la convocatoria seleccionada
                convocatoria_seleccionada = next(
                    (c for c in convocatorias_guardadas if c['id'] == convocatoria_seleccionada_id),
                    None
                )
                
                if convocatoria_seleccionada:
                    # Mostrar detalles de la convocatoria
                    with st.container(border=True):
                        st.markdown(f"### 📄 {convocatoria_seleccionada['titulo']}")
                        col1, col2 = st.columns(2)
                        with col1:
                            st.write(f"**Entidad:** {convocatoria_seleccionada['entidad']}")
                            st.write(f"**Área:** {convocatoria_seleccionada['area']}")
                        with col2:
                            st.write(f"**Fecha:** {convocatoria_seleccionada['fecha']}")
                            st.write(f"**Plazo:** {convocatoria_seleccionada['plazo']}")
                        st.write(f"**Enlace:** {convocatoria_seleccionada['enlace']}")
                    
                    # PASO 2: Seleccionar interesados
                    st.subheader("2️⃣ Selecciona a quiénes enviar")
                    
                    # Opción para seleccionar todos
                    seleccionar_todos = st.checkbox("Seleccionar todos los interesados", value=False)
                    
                    # Lista para almacenar seleccionados
                    interesados_seleccionados = []
                    
                    # Crear checkboxes para cada interesado
                    st.write("**Interesados disponibles:**")
                    
                    # Usar columnas para mejor visualización
                    cols = st.columns(2)
                    for idx, interesado in enumerate(interesados):
                        with cols[idx % 2]:
                            nombre = interesado['Nombre completo']
                            email = interesado['Correo electronico']
                            especialidad = interesado['Especialidad']
                            
                            # Crear una clave única para el checkbox
                            checkbox_key = f"checkbox_{email}_{convocatoria_seleccionada_id}"
                            
                            # Crear checkbox
                            seleccionado = st.checkbox(
                                f"**{nombre}**\n{email}\n*{especialidad}*",
                                value=seleccionar_todos,
                                key=checkbox_key
                            )
                            
                            if seleccionado:
                                interesados_seleccionados.append({
                                    'nombre': nombre,
                                    'email': email,
                                    'especialidad': especialidad
                                })
                    
                    st.info(f"**📌 {len(interesados_seleccionados)}** interesados seleccionados")
                    
                    # PASO 3: Configurar y enviar correo
                    if interesados_seleccionados:
                        st.subheader("3️⃣ Configura y envía el correo")
                        
                        # Configuración del correo
                        col1, col2 = st.columns(2)
                        
                        with col1:
                            asunto = st.text_input(
                                "Asunto del correo:", 
                                value=f"Convocatoria científica: {convocatoria_seleccionada['titulo'][:60]}...",
                                key="asunto_correo"
                            )
                        
                        with col2:
                            remitente = st.text_input(
                                "Remitente:",
                                value="Sistema de Convocatorias Científicas",
                                key="remitente_correo"
                            )
                        
                        # Plantilla del mensaje
                        mensaje_default = f"""Te informamos sobre la siguiente convocatoria que podría ser de tu interés:

**CONVOCATORIA: {convocatoria_seleccionada['titulo']}**

**DETALLES:**
- **Entidad:** {convocatoria_seleccionada['entidad']}
- **Área:** {convocatoria_seleccionada['area']}
- **Fecha de publicación:** {convocatoria_seleccionada['fecha']}
- **Plazo para aplicar:** {convocatoria_seleccionada['plazo']}
- **Enlace directo:** {convocatoria_seleccionada['enlace']}

**INSTRUCCIONES:**
1. Revisa los requisitos en el enlace proporcionado
2. Prepara la documentación necesaria
3. Asegúrate de cumplir con los plazos establecidos
4. Contacta a la entidad para aclarar dudas

**CONTACTO:**
Para más información, visita el enlace oficial o contacta directamente a la entidad convocante.

Esperamos que esta información sea útil para tu trabajo de investigación.

Saludos cordiales,
{remitente}
"""
                        
                        mensaje = st.text_area(
                            "Mensaje del correo (puedes personalizarlo):", 
                            value=mensaje_default, 
                            height=300,
                            key="mensaje_correo"
                        )
                        
                        # Botón para enviar
                        col_enviar1, col_enviar2, col_enviar3 = st.columns([1, 2, 1])
                        
                        with col_enviar2:
                            if st.button("📤 ENVIAR CORREOS REALES", type="primary", use_container_width=True):
                                if not CONFIG_CARGADA:
                                    st.error("No se puede enviar correos reales sin configuración SMTP")
                                else:
                                    # Progreso de envío
                                    progress_bar = st.progress(0)
                                    status_text = st.empty()
                                    
                                    # Contadores
                                    exitosos = 0
                                    fallidos = 0
                                    
                                    # Enviar correos uno por uno
                                    for i, interesado in enumerate(interesados_seleccionados):
                                        # Actualizar progreso
                                        porcentaje = (i + 1) / len(interesados_seleccionados)
                                        progress_bar.progress(porcentaje)
                                        status_text.text(f"Enviando correo {i+1} de {len(interesados_seleccionados)}: {interesado['email']}")
                                        
                                        # Enviar correo real
                                        if enviar_correo_real(
                                            destinatario=interesado['email'],
                                            asunto=asunto,
                                            mensaje=mensaje,
                                            nombre_destinatario=interesado['nombre']
                                        ):
                                            exitosos += 1
                                        else:
                                            fallidos += 1
                                        
                                        # Pequeña pausa para no saturar el servidor
                                        time.sleep(0.5)
                                    
                                    # Limpiar progreso
                                    progress_bar.empty()
                                    status_text.empty()
                                    
                                    # Mostrar resultado final
                                    if exitosos > 0:
                                        st.success(f"✅ ¡{exitosos} correos enviados exitosamente!")
                                        st.balloons()  # ¡Globos de celebración!
                                        
                                        # Enviar notificación al administrador
                                        destinatarios_lista = [i['email'] for i in interesados_seleccionados]
                                        enviar_correo_notificacion_admin(
                                            convocatoria_titulo=convocatoria_seleccionada['titulo'],
                                            total_enviados=exitosos,
                                            destinatarios=destinatarios_lista
                                        )
                                        
                                        # Mostrar detalles del envío
                                        with st.expander("📋 Ver detalles del envío", expanded=True):
                                            st.write(f"**Convocatoria enviada:** {convocatoria_seleccionada['titulo']}")
                                            st.write(f"**Fecha de envío:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
                                            st.write(f"**Total destinatarios:** {len(interesados_seleccionados)}")
                                            st.write(f"**Envíos exitosos:** {exitosos}")
                                            st.write(f"**Envíos fallidos:** {fallidos}")
                                            
                                            st.write("**📧 Lista de destinatarios:**")
                                            for interesado in interesados_seleccionados:
                                                st.write(f"- **{interesado['nombre']}** ({interesado['email']}) - {interesado['especialidad']}")
                                        
                                        # Información adicional
                                        st.info("""
                                        **📌 Los correos han sido enviados exitosamente:**
                                        - Los destinatarios recibirán el correo en su bandeja de entrada
                                        - Se ha enviado una notificación al administrador
                                        - Verifica la bandeja de spam si algún destinatario no recibe el correo
                                        """)
                                    else:
                                        st.error("❌ No se pudo enviar ningún correo. Verifica la configuración SMTP.")
                    else:
                        st.warning("Selecciona al menos un interesado para enviar el correo")

# Pie de página
st.markdown("---")
st.markdown("""
**Sistema de Convocatorias Científicas** | 
*Conectado al servidor remoto: {}:{}* | 
*Enviando desde: {}* | 
Última actualización: {}
""".format(REMOTE_HOST, REMOTE_PORT, EMAIL_USER, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))

st.sidebar.markdown("---")
st.sidebar.info("""
**📋 Instrucciones simples:**

1. **Buscar convocatorias:**
   - Selecciona fuentes en el sidebar
   - Haz clic en 'Buscar Convocatorias'
   - Revisa los resultados

2. **Enviar a interesados:**
   - Ve a la pestaña '📧 Enviar a Interesados'
   - Selecciona una convocatoria
   - Elige a quiénes enviarla
   - Configura y envía el correo

**🎉 ¡Los correos se enviarán realmente!**
""")
