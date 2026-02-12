# -*- coding: utf-8 -*-
import streamlit as st
import requests
import pandas as pd
import time
from datetime import datetime
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
from email.mime.base import MIMEBase
from email import encoders
from email.utils import formatdate
import re

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
# EXACTAMENTE IGUAL que en materias9.py
class Config:
    def __init__(self):
        # Configuración SMTP
        self.SMTP_SERVER = st.secrets["smtp_server"]
        self.SMTP_PORT = st.secrets["smtp_port"]
        self.EMAIL_USER = st.secrets["email_user"]
        self.EMAIL_PASSWORD = st.secrets["email_password"]
        self.NOTIFICATION_EMAIL = st.secrets["notification_email"]
        
        # Configuración remota
        self.REMOTE_HOST = st.secrets["remote_host"]
        self.REMOTE_USER = st.secrets["remote_user"]
        self.REMOTE_PASSWORD = st.secrets["remote_password"]
        self.REMOTE_PORT = st.secrets["remote_port"]
        self.REMOTE_DIR = st.secrets["remote_dir"]
        self.REMOTE_FILE = st.secrets["remote_file"]
        
        # Configuración adicional
        self.MAX_FILE_SIZE_MB = 10
        self.TIMEOUT_SECONDS = 30

CONFIG = Config()

# ==================== FUNCIONES DE VALIDACIÓN ====================
# IGUAL que en materias9.py
def validate_email(email):
    """Valida el formato de un email"""
    pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    return re.match(pattern, email) is not None

def clean_name(name):
    """Limpia y formatea nombres"""
    if not name:
        return name
    name = re.sub(r'[^a-zA-ZáéíóúÁÉÍÓÚñÑ\s]', '', name.strip())
    return ' '.join(word.capitalize() for word in name.split())

# ==================== FUNCIONES SSH/SFTP ====================
# EXACTAMENTE IGUAL que en materias9.py
class SSHManager:
    @staticmethod
    def get_connection():
        """Establece conexión SSH segura - IGUAL que en materias9.py"""
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        try:
            ssh.connect(
                hostname=CONFIG.REMOTE_HOST,
                port=CONFIG.REMOTE_PORT,
                username=CONFIG.REMOTE_USER,
                password=CONFIG.REMOTE_PASSWORD,
                timeout=CONFIG.TIMEOUT_SECONDS
            )
            return ssh
        except Exception as e:
            st.error(f"Error de conexión SSH: {str(e)}")
            return None

    @staticmethod
    def get_remote_file(remote_path):
        """Lee archivo remoto con manejo de errores"""
        ssh = SSHManager.get_connection()
        if not ssh:
            return None
        
        try:
            sftp = ssh.open_sftp()
            with sftp.file(remote_path, 'r') as f:
                content = f.read().decode('utf-8')
            return content
        except Exception as e:
            st.error(f"Error leyendo archivo remoto: {str(e)}")
            return None
        finally:
            ssh.close()

    @staticmethod
    def file_exists(remote_path):
        """Verifica si un archivo existe en el servidor remoto"""
        ssh = SSHManager.get_connection()
        if not ssh:
            return False
        
        try:
            sftp = ssh.open_sftp()
            sftp.stat(remote_path)
            return True
        except:
            return False
        finally:
            ssh.close()

# ==================== FUNCIONES DE ARCHIVOS REMOTOS ====================
def obtener_interesados_activos():
    """Obtiene interesados con estado Activo - VERSIÓN SIMPLIFICADA como en materias9.py"""
    remote_path = os.path.join(CONFIG.REMOTE_DIR, CONFIG.REMOTE_FILE)
    
    # Verificar si el archivo existe
    if not SSHManager.file_exists(remote_path):
        st.warning(f"⚠️ Archivo no encontrado: {remote_path}")
        return []
    
    # Leer archivo
    csv_content = SSHManager.get_remote_file(remote_path)
    if not csv_content:
        return []

    interesados = []
    lines = csv_content.splitlines()
    
    # Verificar encabezados
    if not lines:
        return []
    
    headers = [h.strip().lower() for h in lines[0].split(',')]
    
    # Procesar cada registro
    for line in lines[1:]:
        if not line.strip():
            continue
            
        try:
            parts = [p.strip() for p in line.split(',')]
            if len(parts) < 2:
                continue
                
            # Extraer campos según formato esperado
            registro = {}
            for i, header in enumerate(headers):
                if i < len(parts):
                    registro[header] = parts[i].strip()
            
            # Normalizar campos
            nombre = clean_name(registro.get('nombre completo', ''))
            email = registro.get('correo electronico', '').lower()
            estado = registro.get('estado', '').capitalize()
            especialidad = registro.get('especialidad', 'No especificada')
            
            # Validar email y estado
            if validate_email(email) and estado == 'Activo':
                interesados.append({
                    'nombre': nombre,
                    'email': email,
                    'estado': estado,
                    'especialidad': especialidad,
                    'fecha': registro.get('fecha', '')
                })
        except Exception as e:
            st.warning(f"Error procesando línea: {line[:50]}... Error: {str(e)[:50]}")
            continue
            
    return interesados

# ==================== FUNCIONES DE ENVÍO DE CORREOS ====================
# EXACTAMENTE IGUAL que en materias9.py
def enviar_correo(destinatario, asunto, mensaje, adjunto=None):
    """Envía correo electrónico - EXACTAMENTE IGUAL que en materias9.py"""
    if not destinatario or not asunto or not mensaje:
        st.error("Faltan datos requeridos para enviar el correo")
        return False

    try:
        msg = MIMEMultipart()
        msg['From'] = CONFIG.EMAIL_USER
        msg['To'] = destinatario
        msg['Subject'] = asunto
        msg.attach(MIMEText(mensaje, 'plain'))

        if adjunto:
            if adjunto.size > CONFIG.MAX_FILE_SIZE_MB * 1024 * 1024:
                st.error(f"El archivo excede el tamaño máximo de {CONFIG.MAX_FILE_SIZE_MB}MB")
                return False

            part = MIMEBase('application', 'octet-stream')
            part.set_payload(adjunto.read())
            encoders.encode_base64(part)
            part.add_header('Content-Disposition', f'attachment; filename="{adjunto.name}"')
            msg.attach(part)

        context = ssl.create_default_context()

        with smtplib.SMTP(CONFIG.SMTP_SERVER, CONFIG.SMTP_PORT) as server:
            server.starttls(context=context)
            server.login(CONFIG.EMAIL_USER, CONFIG.EMAIL_PASSWORD)
            server.send_message(msg)

        return True
    except Exception as e:
        st.error(f"Error enviando correo: {str(e)}")
        return False

def probar_conexion_smtp():
    """Prueba la conexión SMTP - IGUAL que en materias9.py"""
    try:
        context = ssl.create_default_context()
        with smtplib.SMTP(CONFIG.SMTP_SERVER, CONFIG.SMTP_PORT, timeout=10) as server:
            server.starttls(context=context)
            server.login(CONFIG.EMAIL_USER, CONFIG.EMAIL_PASSWORD)
        return True, "✅ Conexión SMTP exitosa! Correos listos para enviar."
    except smtplib.SMTPAuthenticationError:
        return False, "❌ Error de autenticación. Verifica tu contraseña de aplicación en secrets.toml"
    except Exception as e:
        return False, f"❌ Error SMTP: {str(e)}"

# ==================== CLASE BUSCADOR DE CONVOCATORIAS ====================
class BuscadorConvocatorias:
    def __init__(self):
        self.user_agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        self.headers = {"User-Agent": self.user_agent}
        self.timeout = 15
    
    def buscar_secihti(self) -> List[Dict]:
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
        
        return convocatorias
    
    def buscar_todas(self) -> List[Dict]:
        """Busca en todas las fuentes disponibles"""
        return self.buscar_secihti()
    
    def guardar_convocatorias(self, convocatorias: List[Dict]):
        """Guarda las convocatorias en un archivo JSON"""
        DATA_DIR = Path("data")
        DATA_DIR.mkdir(exist_ok=True)
        CONVOCATORIAS_FILE = DATA_DIR / "convocatorias.json"
        
        try:
            with open(CONVOCATORIAS_FILE, 'w', encoding='utf-8') as f:
                json.dump(convocatorias, f, ensure_ascii=False, indent=2)
        except:
            pass
    
    def cargar_convocatorias(self) -> List[Dict]:
        """Carga las convocatorias desde el archivo JSON"""
        DATA_DIR = Path("data")
        CONVOCATORIAS_FILE = DATA_DIR / "convocatorias.json"
        
        try:
            if CONVOCATORIAS_FILE.exists():
                with open(CONVOCATORIAS_FILE, 'r', encoding='utf-8') as f:
                    return json.load(f)
            return []
        except:
            return []

# ==================== FUNCIONES DE LOG ====================
def registrar_envio_log(convocatoria_id: str, titulo: str, total: int, exitosos: int):
    """Registra el envío en un archivo CSV de log"""
    DATA_DIR = Path("data")
    DATA_DIR.mkdir(exist_ok=True)
    LOG_FILE = DATA_DIR / "envios_log.csv"
    
    try:
        log_entry = {
            'fecha': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'convocatoria_id': convocatoria_id,
            'titulo': titulo[:100],
            'total_destinatarios': total,
            'envios_exitosos': exitosos,
            'usuario': CONFIG.EMAIL_USER
        }
        
        if not LOG_FILE.exists():
            with open(LOG_FILE, 'w', newline='', encoding='utf-8') as f:
                writer = csv.DictWriter(f, fieldnames=log_entry.keys())
                writer.writeheader()
                writer.writerow(log_entry)
        else:
            with open(LOG_FILE, 'a', newline='', encoding='utf-8') as f:
                writer = csv.DictWriter(f, fieldnames=log_entry.keys())
                writer.writerow(log_entry)
    except:
        pass

def mostrar_historial():
    """Muestra el historial de envíos"""
    DATA_DIR = Path("data")
    LOG_FILE = DATA_DIR / "envios_log.csv"
    
    if not LOG_FILE.exists():
        st.info("📭 No hay registros de envíos aún.")
        return
    
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

# ==================== INTERFAZ PRINCIPAL ====================
def main():
    """Función principal de la aplicación"""
    
    # Verificar configuración básica
    try:
        # Solo verificar que existan los secrets necesarios
        test = CONFIG.EMAIL_USER
    except:
        st.error("""
        ❌ **Error de configuración**
        
        Crea el archivo `.streamlit/secrets.toml` con:
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
    
    # Título
    st.title("🔬 Buscador de Convocatorias Científicas")
    st.markdown("---")
    
    # Estado del sistema
    col1, col2 = st.columns(2)
    with col1:
        st.success(f"✅ SMTP: {CONFIG.EMAIL_USER[:15]}...")
    with col2:
        # Verificar conexión remota
        if SSHManager.file_exists(os.path.join(CONFIG.REMOTE_DIR, CONFIG.REMOTE_FILE)):
            st.success(f"✅ SFTP: Conectado a {CONFIG.REMOTE_HOST}:{CONFIG.REMOTE_PORT}")
        else:
            st.error(f"❌ SFTP: No conectado a {CONFIG.REMOTE_HOST}:{CONFIG.REMOTE_PORT}")
    
    # Sidebar
    with st.sidebar:
        st.header("⚙️ Control")
        
        # Prueba SMTP
        with st.expander("📧 Probar conexión SMTP", expanded=False):
            if st.button("🔌 Probar conexión", use_container_width=True):
                with st.spinner("Probando..."):
                    exito, mensaje = probar_conexion_smtp()
                    if exito:
                        st.success(mensaje)
                    else:
                        st.error(mensaje)
        
        # Cargar interesados
        st.markdown("---")
        st.subheader("👥 Interesados")
        
        if st.button("🔄 Cargar del servidor remoto", use_container_width=True):
            with st.spinner(f"Conectando a {CONFIG.REMOTE_HOST}:{CONFIG.REMOTE_PORT}..."):
                interesados = obtener_interesados_activos()
                if interesados:
                    st.success(f"✅ {len(interesados)} interesados activos")
                    st.session_state.interesados = interesados
                else:
                    st.error("❌ No se pudieron cargar interesados")
        
        # Buscar convocatorias
        st.markdown("---")
        st.subheader("🔍 Convocatorias")
        
        if st.button("🎯 Buscar en SECIHTI", use_container_width=True):
            buscador = BuscadorConvocatorias()
            convocatorias = buscador.buscar_todas()
            buscador.guardar_convocatorias(convocatorias)
            st.session_state.convocatorias = convocatorias
            st.success(f"✅ {len(convocatorias)} convocatorias encontradas")
        
        # Información
        st.markdown("---")
        st.caption(f"**Servidor remoto:** {CONFIG.REMOTE_HOST}:{CONFIG.REMOTE_PORT}")
        st.caption(f"**Archivo:** {CONFIG.REMOTE_FILE}")
    
    # Tabs principales
    tab1, tab2, tab3 = st.tabs(["🔍 Convocatorias", "📧 Enviar", "📊 Historial"])
    
    with tab1:
        st.header("Convocatorias Disponibles")
        
        if 'convocatorias' in st.session_state:
            convocatorias = st.session_state.convocatorias
            for conv in convocatorias:
                with st.container(border=True):
                    st.write(f"**{conv['titulo']}**")
                    st.write(f"🏛️ **Entidad:** {conv['entidad']}")
                    st.write(f"🔗 **Enlace:** {conv['enlace']}")
                    st.write(f"📅 **Publicación:** {conv['fecha']}")
                    st.write(f"⏰ **Plazo:** {conv['plazo']}")
                    
                    if st.button("📌 Seleccionar", key=f"sel_{conv['id']}"):
                        st.session_state.convocatoria_seleccionada = conv
                        st.success("✓ Convocatoria seleccionada")
        else:
            st.info("👈 Busca convocatorias en el sidebar")
    
    with tab2:
        st.header("Envío de Convocatorias")
        
        # Verificar que tengamos convocatoria seleccionada
        if 'convocatoria_seleccionada' not in st.session_state:
            st.warning("⚠️ Primero selecciona una convocatoria en la pestaña 'Convocatorias'")
        else:
            conv = st.session_state.convocatoria_seleccionada
            
            # Mostrar convocatoria seleccionada
            with st.container(border=True):
                st.subheader(f"📄 {conv['titulo']}")
                st.write(f"**{conv['entidad']}**")
            
            # Verificar que tengamos interesados
            if 'interesados' not in st.session_state:
                st.warning("⚠️ Carga interesados desde el sidebar primero")
            else:
                interesados = st.session_state.interesados
                
                # Selector de destinatarios
                st.subheader("Selecciona destinatarios")
                
                seleccionar_todos = st.checkbox("✓ Seleccionar todos")
                
                seleccionados = []
                cols = st.columns(2)
                for i, inv in enumerate(interesados):
                    with cols[i % 2]:
                        nombre = inv.get('nombre', 'Sin nombre')
                        email = inv.get('email', '')
                        especialidad = inv.get('especialidad', 'No especificada')
                        
                        selec = st.checkbox(
                            f"**{nombre[:30]}**\n📧 {email}\n🏷️ {especialidad[:20]}",
                            value=seleccionar_todos,
                            key=f"inv_{i}"
                        )
                        if selec:
                            seleccionados.append({'nombre': nombre, 'email': email})
                
                st.info(f"📌 **{len(seleccionados)}** destinatarios seleccionados")
                
                # Formulario de envío
                if seleccionados:
                    st.markdown("---")
                    st.subheader("Configurar envío")
                    
                    with st.form("form_envio"):
                        asunto = st.text_input(
                            "Asunto del correo*",
                            value=f"Convocatoria: {conv['titulo'][:60]}..."
                        )
                        
                        mensaje_default = f"""
Te informamos sobre la siguiente convocatoria:

🎯 **{conv['titulo']}**
🏛️ **Entidad:** {conv['entidad']}
🔗 **Enlace:** {conv['enlace']}
📅 **Publicación:** {conv['fecha']}
⏰ **Plazo:** {conv['plazo']}

Para más información, consulta el enlace oficial.
"""
                        
                        mensaje = st.text_area(
                            "Mensaje del correo*",
                            value=mensaje_default,
                            height=250
                        )
                        
                        # Parámetros de envío - IGUAL que en materias9.py
                        col1, col2 = st.columns(2)
                        with col1:
                            pausa_correos = st.number_input(
                                "Pausa entre correos (segundos)",
                                min_value=1.0,
                                max_value=5.0,
                                value=2.0,
                                step=0.5
                            )
                        with col2:
                            grupo_size = st.number_input(
                                "Correos por grupo",
                                min_value=1,
                                max_value=10,
                                value=5
                            )
                        
                        pausa_grupos = st.number_input(
                            "Pausa entre grupos (segundos)",
                            min_value=5,
                            max_value=30,
                            value=10
                        )
                        
                        enviar_btn = st.form_submit_button(
                            "📨 ENVIAR CORREOS",
                            type="primary",
                            use_container_width=True
                        )
                    
                    if enviar_btn:
                        if not asunto or not mensaje:
                            st.error("Completa todos los campos obligatorios")
                        else:
                            # Progreso del envío - IGUAL que en materias9.py
                            progress_bar = st.progress(0)
                            status_text = st.empty()
                            
                            exitosos = 0
                            total = len(seleccionados)
                            
                            for i, inv in enumerate(seleccionados):
                                status_text.text(f"📨 Enviando {i+1} de {total}: {inv['email']}")
                                
                                # Personalizar mensaje con nombre
                                mensaje_personalizado = f"Estimado(a) {inv['nombre']}:\n\n{mensaje}"
                                
                                if enviar_correo(inv['email'], asunto, mensaje_personalizado):
                                    exitosos += 1
                                
                                progress_bar.progress((i + 1) / total)
                                time.sleep(pausa_correos)
                                
                                # Pausa entre grupos
                                if (i + 1) % grupo_size == 0 and (i + 1) < total:
                                    status_text.text(f"⏸️ Pausa de {pausa_grupos} segundos...")
                                    time.sleep(pausa_grupos)
                            
                            progress_bar.empty()
                            status_text.empty()
                            
                            # Resultados
                            if exitosos > 0:
                                st.success(f"""
                                ### ✅ ¡Envío completado!
                                - 📨 Total: {total}
                                - ✅ Exitosos: {exitosos}
                                - ❌ Fallidos: {total - exitosos}
                                - 📈 Tasa de éxito: {(exitosos/total*100):.1f}%
                                """)
                                
                                # Registrar en log
                                registrar_envio_log(conv['id'], conv['titulo'], total, exitosos)
                                
                                st.balloons()
                            else:
                                st.error("❌ No se pudo enviar ningún correo")
                else:
                    st.info("👆 Selecciona al menos un destinatario")
    
    with tab3:
        st.header("Historial de Envíos")
        mostrar_historial()

# ==================== EJECUCIÓN ====================
if __name__ == "__main__":
    main()
