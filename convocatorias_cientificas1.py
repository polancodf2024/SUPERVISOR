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
        # Intentar cargar desde secrets.toml
        config['SMTP_SERVER'] = st.secrets.get("smtp_server", "smtp.gmail.com")
        config['SMTP_PORT'] = int(st.secrets.get("smtp_port", 587))
        config['EMAIL_USER'] = st.secrets.get("email_user", "")
        config['EMAIL_PASSWORD'] = st.secrets.get("email_password", "")
        config['NOTIFICATION_EMAIL'] = st.secrets.get("notification_email", "")
        config['REMOTE_HOST'] = st.secrets.get("remote_host", "")
        config['REMOTE_USER'] = st.secrets.get("remote_user", "")
        config['REMOTE_PASSWORD'] = st.secrets.get("remote_password", "")
        config['REMOTE_PORT'] = int(st.secrets.get("remote_port", 22))
        config['REMOTE_DIR'] = st.secrets.get("remote_dir", "")
        config['REMOTE_FILE'] = st.secrets.get("remote_file", "")
        
        # Verificar que los datos esenciales estén presentes
        if (config['EMAIL_USER'] and config['EMAIL_PASSWORD'] and 
            config['SMTP_SERVER'] and config['EMAIL_USER'] != "tu_correo@gmail.com"):
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
REMOTE_HOST = CONFIG['REMOTE_HOST']
REMOTE_USER = CONFIG['REMOTE_USER']
REMOTE_PASSWORD = CONFIG['REMOTE_PASSWORD']
REMOTE_PORT = CONFIG['REMOTE_PORT']
REMOTE_DIR = CONFIG['REMOTE_DIR']
REMOTE_FILE = CONFIG['REMOTE_FILE']
CONFIG_CARGADA = CONFIG['CONFIG_CARGADA']

# ==================== CONFIGURACIÓN DE ARCHIVOS LOCALES ====================
DATA_DIR = Path("data")
DATA_DIR.mkdir(exist_ok=True)
CONVOCATORIAS_FILE = DATA_DIR / "convocatorias.json"
LOG_FILE = DATA_DIR / "envios_log.csv"

# ==================== CONFIGURACIÓN DE DELAYS Y CONTROL DE ENVÍO ====================
PAUSA_ENTRE_CORREOS = 2.0  # segundos entre cada correo
PAUSA_ENTRE_GRUPOS = 10    # segundos entre grupos
GRUPO_SIZE = 5             # correos por grupo
TIMEOUT_SECONDS = 30       # timeout para conexiones SMTP

# ==================== FUNCIONES DE CONEXIÓN REMOTA ====================
def conectar_servidor_remoto():
    """Establece conexión SSH con el servidor remoto"""
    if not all([REMOTE_HOST, REMOTE_USER, REMOTE_PASSWORD]):
        return None
        
    try:
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        ssh.connect(
            REMOTE_HOST, 
            port=REMOTE_PORT, 
            username=REMOTE_USER, 
            password=REMOTE_PASSWORD,
            timeout=10,
            allow_agent=False,
            look_for_keys=False
        )
        return ssh
    except Exception as e:
        # No mostrar error aquí para evitar spam
        return None

def leer_archivo_remoto_directo():
    """Lee el archivo CSV directamente desde el servidor remoto"""
    if not all([REMOTE_HOST, REMOTE_USER, REMOTE_PASSWORD, REMOTE_DIR, REMOTE_FILE]):
        return []
        
    ssh = None
    sftp = None
    try:
        ssh = conectar_servidor_remoto()
        if ssh is None:
            return []
        
        sftp = ssh.open_sftp()
        
        # Verificar si el archivo remoto existe
        try:
            sftp.stat(f"{REMOTE_DIR}/{REMOTE_FILE}")
        except FileNotFoundError:
            return []
        except Exception:
            return []
        
        # Leer contenido del archivo remoto
        with sftp.open(f"{REMOTE_DIR}/{REMOTE_FILE}", 'r') as remote_file:
            contenido = remote_file.read().decode('utf-8-sig')
        
        # Procesar CSV desde el contenido en memoria
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
        
    except Exception:
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
        # Validar correos electrónicos
        validos = []
        for i in activos:
            email = i.get("Correo electronico", "")
            if email and '@' in email and len(email) > 5:
                validos.append(i)
        
        return validos
    except Exception:
        return []

def verificar_conexion_remota():
    """Verifica si hay conexión con el servidor remoto"""
    if not all([REMOTE_HOST, REMOTE_USER, REMOTE_PASSWORD]):
        return False
        
    ssh = None
    try:
        ssh = conectar_servidor_remoto()
        return ssh is not None
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
            return False, "Configuración SMTP no disponible"
        
        context = ssl.create_default_context()
        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT, timeout=10) as server:
            server.starttls(context=context)
            server.login(EMAIL_USER, EMAIL_PASSWORD)
        return True, "Conexión SMTP exitosa"
    except smtplib.SMTPAuthenticationError:
        return False, "Error de autenticación. Usa una contraseña de aplicación de Gmail, no tu contraseña normal."
    except smtplib.SMTPException as e:
        return False, f"Error SMTP: {str(e)}"
    except Exception as e:
        return False, f"Error de conexión: {str(e)}"

def enviar_correo_real(destinatario: str, asunto: str, mensaje: str, 
                      nombre_destinatario: str = "", adjunto_path: Optional[Path] = None,
                      adjunto_filename: Optional[str] = None) -> bool:
    """Envía un correo real usando SMTP con soporte para adjuntos"""
    try:
        if not CONFIG_CARGADA:
            return False
            
        # Crear mensaje
        msg = MIMEMultipart()
        msg['From'] = EMAIL_USER
        msg['To'] = destinatario
        msg['Subject'] = asunto
        msg['Reply-To'] = EMAIL_USER
        
        # Personalizar saludo
        saludo = f"Estimado/a {nombre_destinatario},\n\n" if nombre_destinatario else "Estimado/a investigador/a,\n\n"
        cuerpo_completo = saludo + mensaje
        
        # Agregar firma institucional
        cuerpo_completo += f"""

---
📧 **Sistema Automatizado de Convocatorias Científicas**
🕒 Enviado: {datetime.now().strftime('%d/%m/%Y %H:%M')}
🔬 INCICh - Instituto Nacional de Cardiología

*Este es un mensaje automático, por favor no responder directamente.*
"""
        
        msg.attach(MIMEText(cuerpo_completo, 'plain', 'utf-8'))
        
        # Adjuntar archivo si se proporciona
        if adjunto_path and adjunto_filename and Path(adjunto_path).exists():
            with open(adjunto_path, "rb") as attachment:
                file_data = attachment.read()
                part = MIMEApplication(file_data, Name=adjunto_filename)
                part['Content-Disposition'] = f'attachment; filename="{adjunto_filename}"'
                msg.attach(part)
        
        # Crear contexto SSL
        context = ssl.create_default_context()
        
        # Conectar y enviar con timeout
        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT, timeout=TIMEOUT_SECONDS) as server:
            server.starttls(context=context)
            server.login(EMAIL_USER, EMAIL_PASSWORD)
            server.send_message(msg)
        
        return True
        
    except smtplib.SMTPAuthenticationError:
        st.error("❌ Error de autenticación SMTP. Para Gmail necesitas:")
        st.error("1. Activar verificación en 2 pasos")
        st.error("2. Generar una 'Contraseña de aplicación'")
        st.error("3. Usar esa contraseña de 16 caracteres, no tu contraseña normal")
        return False
    except smtplib.SMTPException as e:
        st.error(f"❌ Error SMTP: {str(e)[:100]}")
        return False
    except Exception as e:
        return False

def enviar_correo_notificacion_admin(convocatoria_titulo: str, total_enviados: int, 
                                    exitosos: int, fallidos: int, destinatarios: List[str]):
    """Envía notificación detallada al administrador"""
    try:
        if not CONFIG_CARGADA or not NOTIFICATION_EMAIL:
            return
            
        asunto = f"📊 Reporte de Envío - {convocatoria_titulo[:40]}... - {datetime.now().strftime('%d/%m/%Y')}"
        
        # Calcular tasa de éxito
        tasa_exito = (exitosos / total_enviados * 100) if total_enviados > 0 else 0
        
        mensaje = f"""
**📋 REPORTE DE ENVÍO DE CONVOCATORIAS**

**📌 Convocatoria:**
{convocatoria_titulo}

**📊 ESTADÍSTICAS:**
✅ Envíos exitosos: {exitosos}
❌ Envíos fallidos: {fallidos}
📨 Total procesados: {total_enviados}
📈 Tasa de éxito: {tasa_exito:.1f}%

**🕒 Fecha y hora:**
{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

**📧 DESTINATARIOS:**
{chr(10).join(f'• {dest[:30]}...' for dest in destinatarios[:20])}
{f'... y {len(destinatarios)-20} más' if len(destinatarios) > 20 else ''}

---
Sistema Automático de Convocatorias INCICh
"""
        
        msg = MIMEMultipart()
        msg['From'] = EMAIL_USER
        msg['To'] = NOTIFICATION_EMAIL
        msg['Subject'] = asunto
        msg.attach(MIMEText(mensaje, 'plain', 'utf-8'))
        
        context = ssl.create_default_context()
        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT, timeout=TIMEOUT_SECONDS) as server:
            server.starttls(context=context)
            server.login(EMAIL_USER, EMAIL_PASSWORD)
            server.send_message(msg)
            
    except Exception:
        pass

def registrar_envio_log(convocatoria_id: str, titulo: str, total: int, exitosos: int):
    """Registra el envío en un archivo CSV de log"""
    try:
        log_entry = {
            'fecha': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'convocatoria_id': convocatoria_id,
            'titulo': titulo,
            'total_destinatarios': total,
            'envios_exitosos': exitosos,
            'usuario': EMAIL_USER if EMAIL_USER else 'demo'
        }
        
        # Crear archivo si no existe
        if not LOG_FILE.exists():
            with open(LOG_FILE, 'w', newline='', encoding='utf-8') as f:
                writer = csv.DictWriter(f, fieldnames=log_entry.keys())
                writer.writeheader()
                writer.writerow(log_entry)
        else:
            with open(LOG_FILE, 'a', newline='', encoding='utf-8') as f:
                writer = csv.DictWriter(f, fieldnames=log_entry.keys())
                writer.writerow(log_entry)
                
    except Exception:
        pass

# ==================== CLASE BUSCADOR DE CONVOCATORIAS ====================
class BuscadorConvocatorias:
    def __init__(self):
        self.user_agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        self.headers = {"User-Agent": self.user_agent}
        self.timeout = 15
    
    def buscar_minciencias(self) -> List[Dict]:
        """Busca convocatorias en Minciencias Colombia"""
        convocatorias = []
        if not BEAUTIFULSOUP_AVAILABLE:
            return convocatorias
            
        try:
            url = "https://minciencias.gov.co/convocatorias"
            response = requests.get(url, headers=self.headers, timeout=self.timeout)
            
            if response.status_code == 200:
                soup = BeautifulSoup(response.content, 'html.parser')
                
                for enlace in soup.find_all('a', href=True):
                    href = enlace['href']
                    texto = enlace.get_text(strip=True)
                    
                    if texto and 'convocatoria' in texto.lower() and len(texto) > 15:
                        convocatorias.append({
                            'id': f"MINC-{len(convocatorias)+1}",
                            'titulo': texto[:150],
                            'entidad': 'Minciencias Colombia',
                            'enlace': href if href.startswith('http') else f"https://minciencias.gov.co{href}",
                            'fecha': datetime.now().strftime("%Y-%m-%d"),
                            'plazo': 'Consultar enlace',
                            'area': 'Investigación',
                            'pais': 'Colombia'
                        })
                        if len(convocatorias) >= 5:
                            break
        except Exception:
            pass
        
        return convocatorias
    
    def buscar_horizonte_europa(self) -> List[Dict]:
        """Busca convocatorias de Horizonte Europa"""
        convocatorias = []
        if not FEEDPARSER_AVAILABLE:
            return convocatorias
            
        try:
            feed_url = "https://ec.europa.eu/info/funding-tenders/opportunities/portal/screen/opportunities/rss-feed"
            feed = feedparser.parse(feed_url)
            
            for i, entry in enumerate(feed.entries[:8]):
                convocatorias.append({
                    'id': f"EU-{i+1}",
                    'titulo': entry.title[:150],
                    'entidad': 'Horizonte Europa',
                    'enlace': entry.link,
                    'fecha': entry.get('published', datetime.now().strftime("%Y-%m-%d"))[:10],
                    'plazo': 'Variable',
                    'area': 'Investigación e Innovación',
                    'pais': 'Unión Europea'
                })
        except Exception:
            pass
        
        return convocatorias
    
    def buscar_conacyt(self) -> List[Dict]:
        """Busca convocatorias en CONACYT México"""
        convocatorias = []
        if not BEAUTIFULSOUP_AVAILABLE:
            return convocatorias
            
        try:
            # URL actualizada de SECIHTI (antes CONACYT)
            url = "https://secihti.mx/convocatorias/"
            response = requests.get(url, headers=self.headers, timeout=self.timeout)
            
            if response.status_code == 200:
                soup = BeautifulSoup(response.content, 'html.parser')
                
                # Agregar convocatoria de ejemplo de SECIHTI
                convocatorias.append({
                    'id': f"SECIHTI-{len(convocatorias)+1}",
                    'titulo': 'Convocatorias Ciencia y Humanidades',
                    'entidad': 'SECIHTI México',
                    'enlace': 'https://secihti.mx/convocatoria_categoria/ciencias-y-humanidades/',
                    'fecha': datetime.now().strftime("%Y-%m-%d"),
                    'plazo': 'Consultar enlace',
                    'area': 'Ciencia y Tecnología',
                    'pais': 'México'
                })
                
                for i, enlace in enumerate(soup.find_all('a', href=True)):
                    texto = enlace.get_text(strip=True)
                    if texto and ('convocatoria' in texto.lower() or 'beca' in texto.lower()) and len(texto) > 20:
                        convocatorias.append({
                            'id': f"CONA-{i+1}",
                            'titulo': texto[:150],
                            'entidad': 'SECIHTI México',
                            'enlace': enlace['href'] if enlace['href'].startswith('http') else f"https://secihti.mx{enlace['href']}",
                            'fecha': datetime.now().strftime("%Y-%m-%d"),
                            'plazo': 'Consultar enlace',
                            'area': 'Ciencia y Tecnología',
                            'pais': 'México'
                        })
                        if len(convocatorias) >= 6:
                            break
        except Exception:
            # Si falla, al menos agregar la convocatoria de ejemplo
            if not convocatorias:
                convocatorias.append({
                    'id': 'SECIHTI-1',
                    'titulo': 'Convocatorias Ciencia y Humanidades',
                    'entidad': 'SECIHTI México',
                    'enlace': 'https://secihti.mx/convocatoria_categoria/ciencias-y-humanidades/',
                    'fecha': datetime.now().strftime("%Y-%m-%d"),
                    'plazo': 'Consultar enlace',
                    'area': 'Ciencia y Tecnología',
                    'pais': 'México'
                })
        
        return convocatorias
    
    def buscar_nsf(self) -> List[Dict]:
        """Busca convocatorias de la National Science Foundation"""
        convocatorias = []
        if not FEEDPARSER_AVAILABLE:
            return convocatorias
            
        try:
            url = "https://www.nsf.gov/rss/funding_opps.xml"
            feed = feedparser.parse(url)
            
            for i, entry in enumerate(feed.entries[:6]):
                convocatorias.append({
                    'id': f"NSF-{i+1}",
                    'titulo': entry.title[:150],
                    'entidad': 'National Science Foundation',
                    'enlace': entry.link,
                    'fecha': entry.get('updated', datetime.now().strftime("%Y-%m-%d"))[:10],
                    'plazo': 'Variable',
                    'area': 'Investigación Científica',
                    'pais': 'Estados Unidos'
                })
        except Exception:
            pass
        
        return convocatorias
    
    def buscar_unesco(self) -> List[Dict]:
        """Busca convocatorias de UNESCO"""
        convocatorias = []
        if not BEAUTIFULSOUP_AVAILABLE:
            return convocatorias
            
        try:
            url = "https://www.unesco.org/en/calls"
            response = requests.get(url, headers=self.headers, timeout=self.timeout)
            
            if response.status_code == 200:
                soup = BeautifulSoup(response.content, 'html.parser')
                
                for i, elemento in enumerate(soup.find_all(['h3', 'h4', 'a'])):
                    texto = elemento.get_text(strip=True)
                    enlace = elemento.get('href', '')
                    
                    if texto and enlace and ('call' in texto.lower() or 'fellowship' in texto.lower()):
                        convocatorias.append({
                            'id': f"UNESCO-{i+1}",
                            'titulo': texto[:150],
                            'entidad': 'UNESCO',
                            'enlace': enlace if enlace.startswith('http') else f"https://www.unesco.org{enlace}",
                            'fecha': datetime.now().strftime("%Y-%m-%d"),
                            'plazo': 'Consultar',
                            'area': 'Educación y Cultura',
                            'pais': 'Internacional'
                        })
                        if len(convocatorias) >= 4:
                            break
        except Exception:
            pass
        
        return convocatorias
    
    def buscar_todas(self, fuentes_seleccionadas: Dict) -> List[Dict]:
        """Busca en todas las fuentes seleccionadas con control de tiempo"""
        todas_convocatorias = []
        
        # Si no hay dependencias, usar datos de ejemplo actualizados
        if not BEAUTIFULSOUP_AVAILABLE and not FEEDPARSER_AVAILABLE:
            return self._datos_ejemplo()
        
        fuentes_activas = []
        if fuentes_seleccionadas.get('minciencias') and BEAUTIFULSOUP_AVAILABLE:
            fuentes_activas.append(('minciencias', self.buscar_minciencias))
        if fuentes_seleccionadas.get('europa') and FEEDPARSER_AVAILABLE:
            fuentes_activas.append(('europa', self.buscar_horizonte_europa))
        if fuentes_seleccionadas.get('conacyt') and BEAUTIFULSOUP_AVAILABLE:
            fuentes_activas.append(('conacyt', self.buscar_conacyt))
        if fuentes_seleccionadas.get('nsf') and FEEDPARSER_AVAILABLE:
            fuentes_activas.append(('nsf', self.buscar_nsf))
        if fuentes_seleccionadas.get('unesco') and BEAUTIFULSOUP_AVAILABLE:
            fuentes_activas.append(('unesco', self.buscar_unesco))
        
        if not fuentes_activas:
            return self._datos_ejemplo()
        
        progress_bar = st.progress(0)
        status_text = st.empty()
        
        for i, (nombre, funcion) in enumerate(fuentes_activas):
            status_text.text(f"🔍 Buscando en {nombre}...")
            try:
                resultados = funcion()
                todas_convocatorias.extend(resultados)
            except Exception:
                pass
            
            progress_bar.progress((i + 1) / len(fuentes_activas))
            time.sleep(0.5)
        
        progress_bar.empty()
        status_text.empty()
        
        # Si no se encontraron resultados, usar datos de ejemplo
        if not todas_convocatorias:
            todas_convocatorias = self._datos_ejemplo()
        
        return todas_convocatorias
    
    def _datos_ejemplo(self) -> List[Dict]:
        """Genera datos de ejemplo para demostración"""
        return [
            {
                'id': 'SECIHTI-2026-1',
                'titulo': 'Convocatorias Ciencia y Humanidades 2026',
                'entidad': 'SECIHTI México',
                'enlace': 'https://secihti.mx/convocatoria_categoria/ciencias-y-humanidades/',
                'fecha': datetime.now().strftime("%Y-%m-%d"),
                'plazo': 'Consultar enlace',
                'area': 'Ciencia y Tecnología',
                'pais': 'México'
            },
            {
                'id': 'EJ-2',
                'titulo': 'Financiamiento para Proyectos de Bioinformática Médica',
                'entidad': 'SECIHTI México',
                'enlace': 'https://secihti.mx/convocatorias/',
                'fecha': datetime.now().strftime("%Y-%m-%d"),
                'plazo': '60 días',
                'area': 'Tecnología',
                'pais': 'México'
            },
            {
                'id': 'EJ-3',
                'titulo': 'Becas para Investigación en Electrónica Médica y Dispositivos',
                'entidad': 'Secretaría de Salud',
                'enlace': 'https://www.gob.mx/salud',
                'fecha': datetime.now().strftime("%Y-%m-%d"),
                'plazo': '45 días',
                'area': 'Ingeniería Biomédica',
                'pais': 'México'
            },
            {
                'id': 'EJ-4',
                'titulo': 'Programa de Apoyo a Proyectos de Investigación e Innovación',
                'entidad': 'UNAM',
                'enlace': 'https://www.unam.mx/investigacion/convocatorias',
                'fecha': datetime.now().strftime("%Y-%m-%d"),
                'plazo': '30 días',
                'area': 'Multidisciplinaria',
                'pais': 'México'
            }
        ]
    
    def guardar_convocatorias(self, convocatorias: List[Dict]):
        """Guarda las convocatorias en un archivo JSON"""
        try:
            with open(CONVOCATORIAS_FILE, 'w', encoding='utf-8') as f:
                json.dump(convocatorias, f, ensure_ascii=False, indent=2)
        except Exception:
            pass
    
    def cargar_convocatorias(self) -> List[Dict]:
        """Carga las convocatorias desde el archivo JSON"""
        try:
            if CONVOCATORIAS_FILE.exists():
                with open(CONVOCATORIAS_FILE, 'r', encoding='utf-8') as f:
                    return json.load(f)
            return []
        except Exception:
            return []

# ==================== FUNCIONES DE INTERFAZ ====================
def mostrar_estado_configuracion():
    """Muestra el estado de la configuración en el sidebar"""
    st.sidebar.markdown("---")
    st.sidebar.subheader("📊 Estado del Sistema")
    
    # Configuración SMTP
    if CONFIG_CARGADA:
        st.sidebar.success(f"✅ SMTP: Configurado ({EMAIL_USER[:15]}...)")
    else:
        st.sidebar.error("❌ SMTP: No configurado o credenciales incorrectas")
        with st.sidebar.expander("📧 Cómo configurar Gmail"):
            st.markdown("""
            1. Activa verificación en 2 pasos
            2. Genera contraseña de aplicación
            3. Usa esa contraseña de 16 caracteres
            """)
    
    # Dependencias
    if BEAUTIFULSOUP_AVAILABLE:
        st.sidebar.success("✅ BeautifulSoup4: OK")
    else:
        st.sidebar.error("❌ BeautifulSoup4: pip install beautifulsoup4")
    
    if FEEDPARSER_AVAILABLE:
        st.sidebar.success("✅ feedparser: OK")
    else:
        st.sidebar.error("❌ feedparser: pip install feedparser")
    
    # Servidor remoto
    if all([REMOTE_HOST, REMOTE_USER, REMOTE_PASSWORD]):
        conectado = verificar_conexion_remota()
        if conectado:
            st.sidebar.success(f"🌐 Servidor: Conectado")
        else:
            st.sidebar.error(f"🌐 Servidor: Desconectado")
    else:
        st.sidebar.warning("🌐 Servidor: No configurado")

def mostrar_configuracion_envio():
    """Muestra la configuración de envío en el sidebar"""
    st.sidebar.markdown("---")
    st.sidebar.subheader("⚙️ Configuración de Envío")
    st.sidebar.info(f"""
    ⏱️ Delay entre emails: {PAUSA_ENTRE_CORREOS}s
    📦 Emails por bloque: {GRUPO_SIZE}
    ⏸️ Delay entre bloques: {PAUSA_ENTRE_GRUPOS}s
    ⏳ Timeout: {TIMEOUT_SECONDS}s
    """)

# ==================== INTERFAZ PRINCIPAL ====================
def main():
    """Función principal de la aplicación"""
    
    # Título y descripción
    st.title("🔬 Buscador de Convocatorias Científicas")
    st.markdown("---")
    
    # Configuración SMTP
    if CONFIG_CARGADA:
        st.success(f"✅ Sistema configurado correctamente | Enviando desde: {EMAIL_USER}")
        
        # Botón para probar conexión SMTP
        with st.expander("📧 Probar conexión SMTP"):
            if st.button("🔌 Probar conexión de correo", key="test_smtp"):
                with st.spinner("Probando conexión SMTP..."):
                    exito, mensaje = probar_conexion_smtp()
                    if exito:
                        st.success(f"✅ {mensaje}")
                    else:
                        st.error(f"❌ {mensaje}")
    else:
        st.warning("""
        **⚠️ Modo demostración - Sin envío real de correos**
        
        Para enviar correos reales, crea `.streamlit/secrets.toml` con:
        ```toml
        email_user = "tu_correo@gmail.com"
        email_password = "xxxx xxxx xxxx xxxx"  # Contraseña de aplicación
        smtp_server = "smtp.gmail.com"
        smtp_port = 587
        notification_email = "admin@ejemplo.com"
        ```
        """)
    
    # Sidebar
    with st.sidebar:
        st.header("⚙️ Configuración")
        
        # Mostrar estado
        mostrar_estado_configuracion()
        
        # Mostrar configuración de envío
        mostrar_configuracion_envio()
        
        # Botón para cargar interesados
        st.markdown("---")
        st.subheader("👥 Interesados Remotos")
        if st.button("🔄 Cargar interesados activos", use_container_width=True):
            with st.spinner("Cargando interesados desde servidor remoto..."):
                interesados = obtener_interesados_activos()
                if interesados:
                    st.success(f"✅ {len(interesados)} interesados activos cargados")
                    st.session_state['interesados_activos'] = interesados
                    st.session_state['ultima_actualizacion'] = datetime.now().strftime('%H:%M:%S')
                else:
                    st.warning("⚠️ No se encontraron interesados activos")
        
        # Mostrar última actualización
        if 'ultima_actualizacion' in st.session_state:
            st.caption(f"🕒 Actualizado: {st.session_state['ultima_actualizacion']}")
        
        # Selección de fuentes
        st.markdown("---")
        st.subheader("🎯 Fuentes de búsqueda")
        
        fuente_minciencias = st.checkbox("Minciencias Colombia", 
                                        value=False,
                                        disabled=not BEAUTIFULSOUP_AVAILABLE)
        fuente_europa = st.checkbox("Horizonte Europa",
                                   value=False,
                                   disabled=not FEEDPARSER_AVAILABLE)
        fuente_conacyt = st.checkbox("SECIHTI México",
                                    value=True,
                                    disabled=not BEAUTIFULSOUP_AVAILABLE)
        fuente_nsf = st.checkbox("NSF (EE.UU.)",
                                value=False,
                                disabled=not FEEDPARSER_AVAILABLE)
        fuente_unesco = st.checkbox("UNESCO",
                                   value=False,
                                   disabled=not BEAUTIFULSOUP_AVAILABLE)
        
        # Instrucciones rápidas
        st.markdown("---")
        st.info("""
        **📋 Instrucciones:**
        1. Selecciona fuentes
        2. Busca convocatorias
        3. Selecciona una
        4. Elige destinatarios
        5. Envía correos
        """)
    
    # Tabs principales
    tab1, tab2, tab3 = st.tabs(["🔍 Buscar Convocatorias", "📧 Enviar a Interesados", "📊 Historial de Envíos"])
    
    with tab1:
        st.header("Búsqueda de Convocatorias")
        
        col1, col2, col3 = st.columns([1, 2, 1])
        with col2:
            buscar_btn = st.button("🔍 BUSCAR CONVOCATORIAS", type="primary", use_container_width=True)
        
        if buscar_btn:
            fuentes = {
                'minciencias': fuente_minciencias,
                'europa': fuente_europa,
                'conacyt': fuente_conacyt,
                'nsf': fuente_nsf,
                'unesco': fuente_unesco
            }
            
            # Inicializar buscador
            buscador = BuscadorConvocatorias()
            
            # Realizar búsqueda
            convocatorias = buscador.buscar_todas(fuentes)
            
            if convocatorias:
                # Guardar convocatorias
                buscador.guardar_convocatorias(convocatorias)
                st.session_state['ultimas_convocatorias'] = convocatorias
                
                # Convertir a DataFrame
                df = pd.DataFrame(convocatorias)
                
                # Eliminar duplicados
                df = df.drop_duplicates(subset=['titulo', 'entidad'])
                
                # Mostrar resultados
                st.subheader(f"📊 Resultados: {len(df)} convocatorias encontradas")
                
                # Mostrar como tabla interactiva
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
                    use_container_width=True  # Deprecated pero funciona
                )
                
                # Opción para descargar - CORREGIDO: use_container_width reemplazado por width
                csv_data = df.to_csv(index=False).encode('utf-8')
                st.download_button(
                    label="📥 Descargar CSV",
                    data=csv_data,
                    file_name=f"convocatorias_{datetime.now().strftime('%Y%m%d_%H%M')}.csv",
                    mime="text/csv"
                )
                
                st.success("✅ Convocatorias guardadas correctamente")
                st.balloons()
            else:
                st.warning("⚠️ No se encontraron convocatorias. Verifica las fuentes seleccionadas.")
    
    with tab2:
        st.header("📧 Envío de Convocatorias a Interesados")
        
        # Inicializar buscador
        buscador = BuscadorConvocatorias()
        
        # Cargar convocatorias guardadas
        if 'ultimas_convocatorias' in st.session_state:
            convocatorias_guardadas = st.session_state['ultimas_convocatorias']
        else:
            convocatorias_guardadas = buscador.cargar_convocatorias()
        
        if not convocatorias_guardadas:
            st.info("""
            **📌 No hay convocatorias disponibles:**
            1. Ve a la pestaña '🔍 Buscar Convocatorias'
            2. Haz clic en 'BUSCAR CONVOCATORIAS'
            3. Regresa a esta pestaña para enviar
            """)
        else:
            # Cargar interesados
            if 'interesados_activos' in st.session_state:
                interesados = st.session_state['interesados_activos']
            else:
                interesados = obtener_interesados_activos()
                if interesados:
                    st.session_state['interesados_activos'] = interesados
            
            if not interesados:
                st.warning("""
                **⚠️ No hay interesados activos:**
                - Haz clic en '🔄 Cargar interesados activos' en el sidebar
                - Verifica la conexión al servidor remoto
                """)
            else:
                # Mostrar estadísticas
                col1, col2, col3 = st.columns(3)
                with col1:
                    st.metric("📋 Convocatorias", len(convocatorias_guardadas))
                with col2:
                    st.metric("👥 Interesados activos", len(interesados))
                with col3:
                    if CONFIG_CARGADA:
                        st.metric("📧 Estado SMTP", "✅ Activo")
                    else:
                        st.metric("📧 Estado SMTP", "❌ Inactivo")
                
                # PASO 1: Seleccionar convocatoria
                st.subheader("1️⃣ Selecciona una convocatoria")
                
                # Crear opciones para el selectbox
                opciones_convocatorias = {}
                for c in convocatorias_guardadas:
                    clave = c['id']
                    valor = f"{c['titulo'][:70]}... - {c['entidad']}"
                    opciones_convocatorias[clave] = valor
                
                convocatoria_seleccionada_id = st.selectbox(
                    "Convocatorias disponibles:",
                    options=list(opciones_convocatorias.keys()),
                    format_func=lambda x: opciones_convocatorias[x],
                    key="select_convocatoria"
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
                            cols = st.columns(4)
                            with cols[0]:
                                st.write(f"**🏛️ Entidad:** {convocatoria_seleccionada['entidad']}")
                            with cols[1]:
                                st.write(f"**🔬 Área:** {convocatoria_seleccionada['area']}")
                            with cols[2]:
                                st.write(f"**🌍 País:** {convocatoria_seleccionada.get('pais', 'No especificado')}")
                            with cols[3]:
                                st.write(f"**📅 Publicación:** {convocatoria_seleccionada['fecha']}")
                            st.write(f"**🔗 Enlace:** {convocatoria_seleccionada['enlace']}")
                            st.write(f"**⏰ Plazo:** {convocatoria_seleccionada['plazo']}")
                        
                        # PASO 2: Seleccionar interesados
                        st.subheader("2️⃣ Selecciona destinatarios")
                        
                        # Filtros de búsqueda
                        col_filtro1, col_filtro2 = st.columns(2)
                        with col_filtro1:
                            busqueda_nombre = st.text_input("🔍 Buscar por nombre:", placeholder="Escribe para filtrar...")
                        with col_filtro2:
                            busqueda_especialidad = st.text_input("🎯 Buscar por especialidad:", placeholder="Ej: Cardiología, Investigación...")
                        
                        # Opción para seleccionar todos
                        seleccionar_todos = st.checkbox("✓ Seleccionar todos los interesados", value=False)
                        
                        # Filtrar interesados
                        interesados_filtrados = interesados.copy()
                        if busqueda_nombre:
                            interesados_filtrados = [
                                i for i in interesados_filtrados 
                                if busqueda_nombre.lower() in i.get('Nombre completo', '').lower()
                            ]
                        if busqueda_especialidad:
                            interesados_filtrados = [
                                i for i in interesados_filtrados 
                                if busqueda_especialidad.lower() in i.get('Especialidad', '').lower()
                            ]
                        
                        # Lista para almacenar seleccionados
                        interesados_seleccionados = []
                        
                        # Mostrar interesados filtrados
                        if interesados_filtrados:
                            st.write(f"**📋 Mostrando {len(interesados_filtrados)} de {len(interesados)} interesados:**")
                            
                            # Crear grid de checkboxes
                            cols = st.columns(2)
                            for idx, interesado in enumerate(interesados_filtrados):
                                with cols[idx % 2]:
                                    nombre = interesado.get('Nombre completo', 'Sin nombre')
                                    email = interesado.get('Correo electronico', '')
                                    especialidad = interesado.get('Especialidad', 'No especificada')
                                    
                                    # Crear checkbox
                                    checkbox_key = f"cb_{email}_{convocatoria_seleccionada_id}_{idx}"
                                    seleccionado = st.checkbox(
                                        f"**{nombre}**\n📧 {email}\n🏷️ {especialidad}",
                                        value=seleccionar_todos,
                                        key=checkbox_key
                                    )
                                    
                                    if seleccionado:
                                        interesados_seleccionados.append({
                                            'nombre': nombre,
                                            'email': email,
                                            'especialidad': especialidad
                                        })
                            
                            st.info(f"**📌 {len(interesados_seleccionados)}** destinatarios seleccionados")
                        else:
                            st.warning("⚠️ No hay interesados que coincidan con los filtros")
                        
                        # PASO 3: Configurar y enviar correo
                        if interesados_seleccionados:
                            st.subheader("3️⃣ Configurar y enviar correo")
                            
                            # Verificar configuración SMTP
                            if not CONFIG_CARGADA:
                                st.error("""
                                **❌ No se puede enviar correos reales**
                                
                                La configuración SMTP no está completa. Los correos se simularán pero no se enviarán.
                                """)
                            
                            # Configuración del correo
                            asunto_default = f"📢 Convocatoria: {convocatoria_seleccionada['titulo'][:80]}..."
                            
                            col_asunto1, col_asunto2 = st.columns([3, 1])
                            with col_asunto1:
                                asunto = st.text_input(
                                    "**Asunto del correo:**",
                                    value=asunto_default,
                                    key="asunto_envio"
                                )
                            with col_asunto2:
                                remitente = st.text_input(
                                    "**Remitente:**",
                                    value="Sistema de Convocatorias INCICh",
                                    key="remitente_envio"
                                )
                            
                            # Plantilla del mensaje
                            mensaje_default = f"""
Te informamos sobre la siguiente convocatoria de financiamiento que podría ser de tu interés:

🎯 **CONVOCATORIA:** {convocatoria_seleccionada['titulo']}

📋 **DETALLES DE LA CONVOCATORIA:**
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🏛️ **Entidad convocante:** {convocatoria_seleccionada['entidad']}
🔬 **Área de investigación:** {convocatoria_seleccionada['area']}
🌍 **País/Región:** {convocatoria_seleccionada.get('pais', 'Internacional')}
📅 **Fecha de publicación:** {convocatoria_seleccionada['fecha']}
⏰ **Plazo límite:** {convocatoria_seleccionada['plazo']}
🔗 **Enlace oficial:** {convocatoria_seleccionada['enlace']}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

📌 **RECOMENDACIONES PARA APLICAR:**
1. Revisa detalladamente los requisitos y bases de la convocatoria
2. Prepara la documentación necesaria con anticipación
3. Verifica fechas límite y horarios de cierre
4. Contacta a la entidad convocante para dudas específicas

💡 **CONTACTO Y SOPORTE:**
Para más información, visita el enlace oficial.

---
🔬 **Instituto Nacional de Cardiología - INCICh**
📧 Sistema de Convocatorias Científicas
🕒 {datetime.now().strftime('%d/%m/%Y %H:%M')}

*Este mensaje fue enviado automáticamente según tus intereses de investigación registrados.*
"""
                            
                            mensaje = st.text_area(
                                "**Mensaje del correo:**",
                                value=mensaje_default,
                                height=350,
                                key="mensaje_envio",
                                help="Puedes personalizar el mensaje antes de enviar"
                            )
                            
                            # Advertencia sobre límites
                            if len(interesados_seleccionados) > 50:
                                st.warning(f"⚠️ Estás a punto de enviar {len(interesados_seleccionados)} correos.")
                            
                            # Botón de envío - CORREGIDO: use_container_width reemplazado
                            col1_btn, col2_btn, col3_btn = st.columns([1, 2, 1])
                            with col2_btn:
                                btn_texto = "📤 ENVIAR CORREOS" if CONFIG_CARGADA else "📤 SIMULAR ENVÍO"
                                btn_tipo = "primary" if CONFIG_CARGADA else "secondary"
                                
                                if st.button(btn_texto, type=btn_tipo):
                                    if not interesados_seleccionados:
                                        st.error("❌ No hay destinatarios seleccionados")
                                    else:
                                        # Variables de control
                                        exitosos = 0
                                        fallidos = 0
                                        
                                        # Elementos de progreso
                                        progress_bar = st.progress(0)
                                        status_text = st.empty()
                                        
                                        total = len(interesados_seleccionados)
                                        
                                        # Enviar correos
                                        for i, interesado in enumerate(interesados_seleccionados):
                                            porcentaje = (i + 1) / total
                                            progress_bar.progress(porcentaje)
                                            status_text.text(f"📨 Enviando {i+1} de {total}: {interesado['email']}")
                                            
                                            if CONFIG_CARGADA:
                                                exito = enviar_correo_real(
                                                    destinatario=interesado['email'],
                                                    asunto=asunto,
                                                    mensaje=mensaje,
                                                    nombre_destinatario=interesado['nombre']
                                                )
                                            else:
                                                time.sleep(0.2)
                                                exito = True
                                            
                                            if exito:
                                                exitosos += 1
                                            else:
                                                fallidos += 1
                                            
                                            if CONFIG_CARGADA:
                                                time.sleep(PAUSA_ENTRE_CORREOS)
                                                if (i + 1) % GRUPO_SIZE == 0 and (i + 1) < total:
                                                    status_text.text(f"⏸️ Pausa de {PAUSA_ENTRE_GRUPOS}s...")
                                                    time.sleep(PAUSA_ENTRE_GRUPOS)
                                        
                                        progress_bar.empty()
                                        status_text.empty()
                                        
                                        if exitosos > 0:
                                            st.success(f"""
                                            ### ✅ ¡Envío completado!
                                            
                                            **📊 RESUMEN:**
                                            - ✅ Exitosos: {exitosos}
                                            - ❌ Fallidos: {fallidos}
                                            - 📈 Tasa: {(exitosos/total*100):.1f}%
                                            """)
                                            
                                            registrar_envio_log(
                                                convocatoria_seleccionada['id'],
                                                convocatoria_seleccionada['titulo'],
                                                total,
                                                exitosos
                                            )
                                            
                                            if CONFIG_CARGADA and NOTIFICATION_EMAIL:
                                                destinatarios_lista = [i['email'] for i in interesados_seleccionados]
                                                enviar_correo_notificacion_admin(
                                                    convocatoria_seleccionada['titulo'],
                                                    total, exitosos, fallidos,
                                                    destinatarios_lista
                                                )
                                            
                                            st.balloons()
                                        else:
                                            st.error("❌ No se pudo enviar ningún correo.")
                        else:
                            st.info("👆 **Selecciona al menos un destinatario**")
    
    with tab3:
        st.header("📊 Historial de Envíos")
        
        if LOG_FILE.exists():
            try:
                df_log = pd.read_csv(LOG_FILE)
                df_log['fecha'] = pd.to_datetime(df_log['fecha'])
                df_log = df_log.sort_values('fecha', ascending=False)
                
                col1, col2, col3 = st.columns(3)
                with col1:
                    st.metric("📨 Total de envíos", len(df_log))
                with col2:
                    st.metric("👥 Destinatarios", df_log['total_destinatarios'].sum())
                with col3:
                    st.metric("✅ Éxitos", df_log['envios_exitosos'].sum())
                
                st.dataframe(
                    df_log,
                    column_config={
                        "fecha": st.column_config.DatetimeColumn("Fecha", format="DD/MM/YYYY HH:mm"),
                        "titulo": st.column_config.TextColumn("Convocatoria", width="large"),
                        "total_destinatarios": "Total",
                        "envios_exitosos": "Exitosos",
                    },
                    hide_index=True,
                    use_container_width=True
                )
                
                csv_log = df_log.to_csv(index=False).encode('utf-8')
                st.download_button(
                    label="📥 Descargar historial",
                    data=csv_log,
                    file_name=f"historial_{datetime.now().strftime('%Y%m%d')}.csv",
                    mime="text/csv"
                )
                
            except Exception as e:
                st.error(f"Error al cargar historial: {e}")
        else:
            st.info("📭 No hay registros de envíos aún.")
    
    # Pie de página
    st.markdown("---")
    cols_footer = st.columns(3)
    with cols_footer[0]:
        st.caption(f"© {datetime.now().year} - INCICh")
    with cols_footer[1]:
        if CONFIG_CARGADA:
            st.caption("📧 SMTP: Activo")
        else:
            st.caption("📧 SMTP: Demo")
    with cols_footer[2]:
        st.caption(f"🕒 {datetime.now().strftime('%d/%m/%Y %H:%M')}")

# ==================== EJECUCIÓN PRINCIPAL ====================
if __name__ == "__main__":
    main()
