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
    page_title="Buscador de Convocatorias Nacionales",
    page_icon="🇲🇽",
    layout="wide",
    initial_sidebar_state="expanded"
)

# ==================== CONFIGURACIÓN DE STREAMLIT SECRETS ====================
class Config:
    def __init__(self):
        # Configuración SMTP
        self.SMTP_SERVER = st.secrets["smtp_server"]
        self.SMTP_PORT = st.secrets["smtp_port"]
        self.EMAIL_USER = st.secrets["email_user"]
        self.EMAIL_PASSWORD = st.secrets["email_password"].replace(" ", "")
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
def validate_email(email):
    pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    return re.match(pattern, email) is not None

def clean_name(name):
    if not name:
        return name
    name = re.sub(r'[^a-zA-ZáéíóúÁÉÍÓÚñÑ\s]', '', name.strip())
    return ' '.join(word.capitalize() for word in name.split())

# ==================== FUNCIONES SSH/SFTP ====================
class SSHManager:
    @staticmethod
    def get_connection():
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
        ssh = SSHManager.get_connection()
        if not ssh:
            return None
        
        try:
            sftp = ssh.open_sftp()
            with sftp.file(remote_path, 'r') as f:
                content = f.read().decode('utf-8')
            return content
        except Exception as e:
            return None
        finally:
            ssh.close()

    @staticmethod
    def file_exists(remote_path):
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
    remote_path = os.path.join(CONFIG.REMOTE_DIR, CONFIG.REMOTE_FILE)
    
    if not SSHManager.file_exists(remote_path):
        return []
    
    csv_content = SSHManager.get_remote_file(remote_path)
    if not csv_content:
        return []

    interesados = []
    lines = csv_content.splitlines()
    
    if not lines:
        return []
    
    headers = [h.strip().lower() for h in lines[0].split(',')]
    
    for line in lines[1:]:
        if not line.strip():
            continue
            
        try:
            parts = [p.strip() for p in line.split(',')]
            if len(parts) < 2:
                continue
                
            registro = {}
            for i, header in enumerate(headers):
                if i < len(parts):
                    registro[header] = parts[i].strip()
            
            nombre = clean_name(registro.get('nombre completo', ''))
            email = registro.get('correo electronico', '').lower()
            estado = registro.get('estado', '').capitalize()
            especialidad = registro.get('especialidad', 'No especificada')
            
            if validate_email(email) and estado == 'Activo':
                interesados.append({
                    'nombre': nombre,
                    'email': email,
                    'estado': estado,
                    'especialidad': especialidad,
                    'fecha': registro.get('fecha', '')
                })
        except:
            continue
            
    return interesados

# ==================== FUNCIONES DE ENVÍO DE CORREOS ====================
def enviar_correo(destinatario, asunto, mensaje, adjunto=None):
    if not destinatario or not asunto or not mensaje:
        return False

    try:
        msg = MIMEMultipart()
        msg['From'] = CONFIG.EMAIL_USER
        msg['To'] = destinatario
        msg['Subject'] = asunto
        msg.attach(MIMEText(mensaje, 'plain'))

        if adjunto:
            part = MIMEBase('application', 'octet-stream')
            part.set_payload(adjunto.read())
            encoders.encode_base64(part)
            part.add_header('Content-Disposition', f'attachment; filename="{adjunto.name}"')
            msg.attach(part)

        context = ssl.create_default_context()
        with smtplib.SMTP(CONFIG.SMTP_SERVER, CONFIG.SMTP_PORT, timeout=30) as server:
            server.starttls(context=context)
            server.login(CONFIG.EMAIL_USER, CONFIG.EMAIL_PASSWORD)
            server.send_message(msg)

        return True
    except:
        return False

# ==================== BUSCADOR DE CONVOCATORIAS NACIONALES ====================
class BuscadorConvocatoriasNacionales:
    def __init__(self):
        self.user_agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        self.headers = {"User-Agent": self.user_agent}
        self.timeout = 15
        self.fecha_actual = datetime.now().strftime("%Y-%m-%d")
    
    def buscar_secihti(self) -> List[Dict]:
        """Busca convocatorias en SECIHTI (antes CONACYT) - Principal fuente nacional"""
        convocatorias = []
        
        # Fuente 1: Ciencia y Humanidades
        convocatorias.append({
            'id': f'SECIHTI-CYH-{datetime.now().strftime("%Y%m%d")}',
            'titulo': 'Convocatorias Ciencia y Humanidades 2026',
            'entidad': 'SECIHTI - Secretaría de Ciencia, Humanidades, Tecnología e Innovación',
            'enlace': 'https://secihti.mx/convocatoria_categoria/ciencias-y-humanidades/',
            'fecha': self.fecha_actual,
            'plazo': 'Consultar en convocatoria',
            'area': 'Ciencias y Humanidades',
            'pais': 'México',
            'institucion': 'SECIHTI',
            'tipo': 'Investigación'
        })
        
        # Fuente 2: Becas Nacionales
        convocatorias.append({
            'id': f'SECIHTI-BECAS-{datetime.now().strftime("%Y%m%d")}',
            'titulo': 'Becas Nacionales para Estudios de Posgrado 2026',
            'entidad': 'SECIHTI - Becas Nacionales',
            'enlace': 'https://secihti.mx/becas-nacionales/',
            'fecha': self.fecha_actual,
            'plazo': 'Consultar convocatoria',
            'area': 'Posgrado',
            'pais': 'México',
            'institucion': 'SECIHTI',
            'tipo': 'Beca'
        })
        
        # Fuente 3: Cátedras
        convocatorias.append({
            'id': f'SECIHTI-CATEDRAS-{datetime.now().strftime("%Y%m%d")}',
            'titulo': 'Cátedras CONAHCYT para Jóvenes Investigadores 2026',
            'entidad': 'SECIHTI - Cátedras',
            'enlace': 'https://secihti.mx/catedras/',
            'fecha': self.fecha_actual,
            'plazo': 'Consultar',
            'area': 'Investigación',
            'pais': 'México',
            'institucion': 'SECIHTI',
            'tipo': 'Cátedra'
        })
        
        return convocatorias
    
    def buscar_unam(self) -> List[Dict]:
        """Busca convocatorias en UNAM"""
        convocatorias = []
        
        # Fuente 4: DGAPA - PAPIIT
        convocatorias.append({
            'id': f'UNAM-PAPIIT-{datetime.now().strftime("%Y%m%d")}',
            'titulo': 'Programa de Apoyo a Proyectos de Investigación e Innovación Tecnológica (PAPIIT) 2026',
            'entidad': 'UNAM - DGAPA',
            'enlace': 'https://dgapa.unam.mx/index.php/aypapiit',
            'fecha': self.fecha_actual,
            'plazo': 'Por publicar',
            'area': 'Investigación',
            'pais': 'México',
            'institucion': 'UNAM',
            'tipo': 'Proyecto'
        })
        
        # Fuente 5: PAPIME
        convocatorias.append({
            'id': f'UNAM-PAPIME-{datetime.now().strftime("%Y%m%d")}',
            'titulo': 'Programa de Apoyo a Proyectos para la Innovación y Mejoramiento de la Enseñanza (PAPIME) 2026',
            'entidad': 'UNAM - DGAPA',
            'enlace': 'https://dgapa.unam.mx/index.php/aypapime',
            'fecha': self.fecha_actual,
            'plazo': 'Por publicar',
            'area': 'Docencia',
            'pais': 'México',
            'institucion': 'UNAM',
            'tipo': 'Proyecto'
        })
        
        # Fuente 6: PASPA
        convocatorias.append({
            'id': f'UNAM-PASPA-{datetime.now().strftime("%Y%m%d")}',
            'titulo': 'Programa de Apoyos para la Superación del Personal Académico (PASPA) 2026',
            'entidad': 'UNAM - DGAPA',
            'enlace': 'https://dgapa.unam.mx/index.php/aypaspa',
            'fecha': self.fecha_actual,
            'plazo': 'Por publicar',
            'area': 'Movilidad',
            'pais': 'México',
            'institucion': 'UNAM',
            'tipo': 'Beca'
        })
        
        return convocatorias
    
    def buscar_ipn(self) -> List[Dict]:
        """Busca convocatorias en IPN"""
        convocatorias = []
        
        # Fuente 7: SIP - Investigación
        convocatorias.append({
            'id': f'IPN-SIP-{datetime.now().strftime("%Y%m%d")}',
            'titulo': 'Convocatoria de Investigación Científica y Desarrollo Tecnológico 2026',
            'entidad': 'IPN - Secretaría de Investigación y Posgrado',
            'enlace': 'https://www.ipn.mx/investigacion/convocatorias/',
            'fecha': self.fecha_actual,
            'plazo': 'Por publicar',
            'area': 'Investigación',
            'pais': 'México',
            'institucion': 'IPN',
            'tipo': 'Proyecto'
        })
        
        # Fuente 8: COFAA - Becas
        convocatorias.append({
            'id': f'IPN-COFAA-{datetime.now().strftime("%Y%m%d")}',
            'titulo': 'Becas COFAA para Estudios de Posgrado 2026',
            'entidad': 'IPN - COFAA',
            'enlace': 'https://www.cofaa.ipn.mx/',
            'fecha': self.fecha_actual,
            'plazo': 'Por publicar',
            'area': 'Posgrado',
            'pais': 'México',
            'institucion': 'IPN',
            'tipo': 'Beca'
        })
        
        return convocatorias
    
    def buscar_salud(self) -> List[Dict]:
        """Busca convocatorias en Sector Salud"""
        convocatorias = []
        
        # Fuente 9: IMSS - Investigación
        convocatorias.append({
            'id': f'IMSS-INV-{datetime.now().strftime("%Y%m%d")}',
            'titulo': 'Convocatoria de Investigación en Salud 2026',
            'entidad': 'IMSS - Coordinación de Investigación en Salud',
            'enlace': 'http://www.imss.gob.mx/investigacion',
            'fecha': self.fecha_actual,
            'plazo': 'Por publicar',
            'area': 'Salud',
            'pais': 'México',
            'institucion': 'IMSS',
            'tipo': 'Investigación'
        })
        
        # Fuente 10: INC - Cardiología
        convocatorias.append({
            'id': f'INC-INV-{datetime.now().strftime("%Y%m%d")}',
            'titulo': 'Convocatoria de Investigación en Cardiología 2026',
            'entidad': 'Instituto Nacional de Cardiología - INCICh',
            'enlace': 'https://www.gob.mx/salud/acciones-y-programas/convocatorias',
            'fecha': self.fecha_actual,
            'plazo': 'Por publicar',
            'area': 'Cardiología',
            'pais': 'México',
            'institucion': 'INCICh',
            'tipo': 'Investigación'
        })
        
        return convocatorias
    
    def buscar_energia(self) -> List[Dict]:
        """Busca convocatorias en Sector Energía"""
        convocatorias = []
        
        # Fuente 11: SENER - Energía
        convocatorias.append({
            'id': f'SENER-{datetime.now().strftime("%Y%m%d")}',
            'titulo': 'Fondo Sectorial CONACYT-SENER-Hidrocarburos 2026',
            'entidad': 'SENER - Secretaría de Energía',
            'enlace': 'https://www.gob.mx/sener',
            'fecha': self.fecha_actual,
            'plazo': 'Por publicar',
            'area': 'Energía',
            'pais': 'México',
            'institucion': 'SENER',
            'tipo': 'Fondo Sectorial'
        })
        
        return convocatorias
    
    def buscar_agricultura(self) -> List[Dict]:
        """Busca convocatorias en Sector Agropecuario"""
        convocatorias = []
        
        # Fuente 12: INIFAP
        convocatorias.append({
            'id': f'INIFAP-{datetime.now().strftime("%Y%m%d")}',
            'titulo': 'Convocatoria de Investigación Agropecuaria 2026',
            'entidad': 'INIFAP - Instituto Nacional de Investigaciones Forestales',
            'enlace': 'https://www.gob.mx/inifap',
            'fecha': self.fecha_actual,
            'plazo': 'Por publicar',
            'area': 'Agropecuario',
            'pais': 'México',
            'institucion': 'INIFAP',
            'tipo': 'Investigación'
        })
        
        return convocatorias
    
    def buscar_todas(self) -> List[Dict]:
        """Busca TODAS las convocatorias nacionales"""
        todas_convocatorias = []
        
        # Progreso
        progress_bar = st.progress(0)
        status_text = st.empty()
        
        fuentes = [
            ("SECIHTI", self.buscar_secihti),
            ("UNAM", self.buscar_unam),
            ("IPN", self.buscar_ipn),
            ("SALUD", self.buscar_salud),
            ("ENERGÍA", self.buscar_energia),
            ("AGRICULTURA", self.buscar_agricultura)
        ]
        
        for i, (nombre, fuente) in enumerate(fuentes):
            status_text.text(f"🔍 Buscando convocatorias en {nombre}...")
            try:
                resultados = fuente()
                todas_convocatorias.extend(resultados)
                time.sleep(0.5)
            except Exception as e:
                st.warning(f"Error en {nombre}: {str(e)[:50]}")
            
            progress_bar.progress((i + 1) / len(fuentes))
        
        progress_bar.empty()
        status_text.empty()
        
        return todas_convocatorias
    
    def guardar_convocatorias(self, convocatorias: List[Dict]):
        DATA_DIR = Path("data")
        DATA_DIR.mkdir(exist_ok=True)
        CONVOCATORIAS_FILE = DATA_DIR / "convocatorias_nacionales.json"
        
        try:
            with open(CONVOCATORIAS_FILE, 'w', encoding='utf-8') as f:
                json.dump(convocatorias, f, ensure_ascii=False, indent=2)
        except:
            pass
    
    def cargar_convocatorias(self) -> List[Dict]:
        DATA_DIR = Path("data")
        CONVOCATORIAS_FILE = DATA_DIR / "convocatorias_nacionales.json"
        
        try:
            if CONVOCATORIAS_FILE.exists():
                with open(CONVOCATORIAS_FILE, 'r', encoding='utf-8') as f:
                    return json.load(f)
            return []
        except:
            return []

# ==================== FUNCIONES DE LOG ====================
def registrar_envio_log(convocatoria_id: str, titulo: str, total: int, exitosos: int):
    DATA_DIR = Path("data")
    DATA_DIR.mkdir(exist_ok=True)
    LOG_FILE = DATA_DIR / "envios_log.csv"
    
    try:
        log_entry = {
            'fecha': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'convocatoria_id': convocatoria_id,
            'titulo': titulo[:100],
            'institucion': titulo.split('-')[0].strip() if '-' in titulo else 'General',
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
    DATA_DIR = Path("data")
    LOG_FILE = DATA_DIR / "envios_log.csv"
    
    if not LOG_FILE.exists():
        st.info("📭 No hay registros de envíos aún.")
        return
    
    try:
        df_log = pd.read_csv(LOG_FILE)
        df_log['fecha'] = pd.to_datetime(df_log['fecha'])
        df_log = df_log.sort_values('fecha', ascending=False)
        
        col1, col2, col3, col4 = st.columns(4)
        with col1:
            st.metric("📨 Total de envíos", len(df_log))
        with col2:
            st.metric("👥 Destinatarios", df_log['total_destinatarios'].sum())
        with col3:
            st.metric("✅ Éxitos", df_log['envios_exitosos'].sum())
        with col4:
            st.metric("🏛️ Instituciones", df_log['institucion'].nunique())
        
        st.dataframe(
            df_log,
            column_config={
                "fecha": st.column_config.DatetimeColumn("Fecha", format="DD/MM/YYYY HH:mm"),
                "titulo": st.column_config.TextColumn("Convocatoria", width="large"),
                "institucion": "Institución",
                "total_destinatarios": "Total",
                "envios_exitosos": "Exitosos",
            },
            hide_index=True,
            use_container_width=True
        )
        
        csv_log = df_log.to_csv(index=False).encode('utf-8')
        st.download_button(
            label="📥 Descargar historial completo",
            data=csv_log,
            file_name=f"historial_convocatorias_{datetime.now().strftime('%Y%m%d')}.csv",
            mime="text/csv"
        )
    except Exception as e:
        st.error(f"Error al cargar historial: {e}")

# ==================== INTERFAZ PRINCIPAL ====================
def main():
    # Verificar configuración
    try:
        test = CONFIG.EMAIL_USER
    except:
        st.error("❌ Error de configuración. Verifica secrets.toml")
        st.stop()
    
    # Título
    st.title("🇲🇽 Buscador de Convocatorias Nacionales")
    st.markdown("---")
    
    # Estado del sistema
    col1, col2, col3 = st.columns(3)
    with col1:
        st.success(f"✅ SMTP: {CONFIG.EMAIL_USER[:15]}...")
    with col2:
        if SSHManager.file_exists(os.path.join(CONFIG.REMOTE_DIR, CONFIG.REMOTE_FILE)):
            st.success(f"✅ SFTP: {CONFIG.REMOTE_HOST}:{CONFIG.REMOTE_PORT}")
        else:
            st.error(f"❌ SFTP: Desconectado")
    with col3:
        st.info(f"📅 {datetime.now().strftime('%d/%m/%Y')}")
    
    # Sidebar
    with st.sidebar:
        st.header("⚙️ Control")
        
        # Prueba SMTP
        with st.expander("📧 Probar conexión SMTP"):
            if st.button("🔌 Probar", use_container_width=True):
                try:
                    context = ssl.create_default_context()
                    with smtplib.SMTP(CONFIG.SMTP_SERVER, CONFIG.SMTP_PORT, timeout=10) as server:
                        server.starttls(context=context)
                        server.login(CONFIG.EMAIL_USER, CONFIG.EMAIL_PASSWORD)
                    st.success("✅ Conexión exitosa!")
                except Exception as e:
                    st.error(f"❌ Error: {str(e)[:50]}")
        
        # Cargar interesados
        st.markdown("---")
        st.subheader("👥 Interesados")
        if st.button("🔄 Cargar lista remota", use_container_width=True):
            with st.spinner("Cargando..."):
                interesados = obtener_interesados_activos()
                if interesados:
                    st.success(f"✅ {len(interesados)} interesados activos")
                    st.session_state.interesados = interesados
                else:
                    st.error("❌ No se cargaron interesados")
        
        # Buscar convocatorias
        st.markdown("---")
        st.subheader("🔍 Convocatorias")
        if st.button("🎯 Buscar TODAS", use_container_width=True):
            buscador = BuscadorConvocatoriasNacionales()
            with st.spinner("Buscando en todas las instituciones..."):
                convocatorias = buscador.buscar_todas()
                if convocatorias:
                    buscador.guardar_convocatorias(convocatorias)
                    st.session_state.convocatorias = convocatorias
                    st.success(f"✅ {len(convocatorias)} convocatorias encontradas")
                else:
                    st.error("❌ No se encontraron convocatorias")
        
        # Filtros
        st.markdown("---")
        st.subheader("🎯 Filtros")
        
        if 'convocatorias' in st.session_state:
            instituciones = list(set([c['institucion'] for c in st.session_state.convocatorias]))
            tipos = list(set([c['tipo'] for c in st.session_state.convocatorias]))
            
            filtro_institucion = st.multiselect("Institución", instituciones, default=instituciones)
            filtro_tipo = st.multiselect("Tipo", tipos, default=tipos)
            
            st.session_state.filtro_institucion = filtro_institucion
            st.session_state.filtro_tipo = filtro_tipo
        
        # Información
        st.markdown("---")
        st.caption("**Fuentes nacionales:**")
        st.caption("• SECIHTI (antes CONACYT)")
        st.caption("• UNAM - DGAPA")
        st.caption("• IPN - SIP/COFAA")
        st.caption("• Sector Salud (IMSS, INC)")
        st.caption("• SENER - Energía")
        st.caption("• INIFAP - Agricultura")
    
    # Tabs principales
    tab1, tab2, tab3 = st.tabs(["📋 Convocatorias", "📧 Enviar", "📊 Estadísticas"])
    
    with tab1:
        st.header("Convocatorias Nacionales Vigentes")
        
        if 'convocatorias' in st.session_state:
            convocatorias = st.session_state.convocatorias
            
            # Aplicar filtros
            if 'filtro_institucion' in st.session_state and st.session_state.filtro_institucion:
                convocatorias = [c for c in convocatorias if c['institucion'] in st.session_state.filtro_institucion]
            if 'filtro_tipo' in st.session_state and st.session_state.filtro_tipo:
                convocatorias = [c for c in convocatorias if c['tipo'] in st.session_state.filtro_tipo]
            
            # Mostrar estadísticas rápidas
            col1, col2, col3, col4 = st.columns(4)
            with col1:
                st.metric("Total", len(convocatorias))
            with col2:
                st.metric("Instituciones", len(set([c['institucion'] for c in convocatorias])))
            with col3:
                st.metric("Investigación", len([c for c in convocatorias if c['tipo'] == 'Investigación']))
            with col4:
                st.metric("Becas", len([c for c in convocatorias if c['tipo'] == 'Beca']))
            
            # Mostrar convocatorias agrupadas por institución
            for institucion in sorted(set([c['institucion'] for c in convocatorias])):
                with st.expander(f"🏛️ {institucion} ({len([c for c in convocatorias if c['institucion'] == institucion])})"):
                    for conv in [c for c in convocatorias if c['institucion'] == institucion]:
                        with st.container(border=True):
                            col1, col2 = st.columns([3, 1])
                            with col1:
                                st.write(f"**{conv['titulo']}**")
                                st.write(f"📌 **Tipo:** {conv['tipo']} | 🏛️ **Entidad:** {conv['entidad']}")
                                st.write(f"🔗 **Enlace:** {conv['enlace']}")
                                st.write(f"📅 **Publicación:** {conv['fecha']} | ⏰ **Plazo:** {conv['plazo']}")
                            with col2:
                                if st.button("📌 Seleccionar", key=f"sel_{conv['id']}"):
                                    st.session_state.convocatoria_seleccionada = conv
                                    st.success("✓ Seleccionada")
        else:
            st.info("👈 Busca convocatorias en el sidebar")
    
    with tab2:
        st.header("Envío de Convocatorias")
        
        if 'convocatoria_seleccionada' not in st.session_state:
            st.warning("⚠️ Selecciona una convocatoria en la pestaña 'Convocatorias'")
        elif 'interesados' not in st.session_state:
            st.warning("⚠️ Carga interesados desde el sidebar")
        else:
            conv = st.session_state.convocatoria_seleccionada
            interesados = st.session_state.interesados
            
            # Mostrar convocatoria seleccionada
            with st.container(border=True):
                st.subheader(f"📄 {conv['titulo']}")
                st.write(f"**{conv['entidad']}**")
            
            # Selector de destinatarios
            st.subheader("Selecciona destinatarios")
            seleccionar_todos = st.checkbox("✓ Seleccionar todos")
            
            seleccionados = []
            cols = st.columns(2)
            for i, inv in enumerate(interesados):
                with cols[i % 2]:
                    nombre = inv.get('nombre', 'Sin nombre')[:30]
                    email = inv.get('email', '')
                    especialidad = inv.get('especialidad', 'General')[:20]
                    
                    if st.checkbox(
                        f"**{nombre}**\n📧 {email}\n🏷️ {especialidad}",
                        value=seleccionar_todos,
                        key=f"inv_{i}"
                    ):
                        seleccionados.append({'nombre': nombre, 'email': email})
            
            st.info(f"📌 **{len(seleccionados)}** destinatarios seleccionados")
            
            # Formulario de envío
            if seleccionados:
                st.markdown("---")
                with st.form("form_envio"):
                    asunto = st.text_input(
                        "Asunto*",
                        value=f"🇲🇽 Convocatoria Nacional: {conv['titulo'][:60]}..."
                    )
                    
                    mensaje_default = f"""
Estimado(a) investigador(a):

La **{conv['entidad']}** ha publicado la siguiente convocatoria nacional:

🎯 **{conv['titulo']}**
🏛️ **Institución:** {conv['institucion']}
📌 **Tipo:** {conv['tipo']}
🔗 **Enlace oficial:** {conv['enlace']}
📅 **Publicación:** {conv['fecha']}
⏰ **Cierre:** {conv['plazo']}

📋 **Requisitos generales:**
• Revisar bases en el enlace oficial
• Preparar documentación requerida
• Verificar fechas límite

Atentamente,
**Sistema de Convocatorias Nacionales**
INCICh - Instituto Nacional de Cardiología
"""
                    
                    mensaje = st.text_area("Mensaje*", value=mensaje_default, height=300)
                    
                    col1, col2 = st.columns(2)
                    with col1:
                        pausa = st.number_input("Pausa entre correos (s)", 1.0, 5.0, 2.0, 0.5)
                    with col2:
                        grupo = st.number_input("Correos por grupo", 1, 10, 5)
                    
                    pausa_grupo = st.number_input("Pausa entre grupos (s)", 5, 30, 10)
                    
                    if st.form_submit_button("📨 ENVIAR CORREOS", type="primary", use_container_width=True):
                        if not asunto or not mensaje:
                            st.error("Completa todos los campos")
                        else:
                            progress = st.progress(0)
                            status = st.empty()
                            
                            exitosos = 0
                            total = len(seleccionados)
                            
                            for i, inv in enumerate(seleccionados):
                                status.text(f"📨 {i+1}/{total}: {inv['email']}")
                                
                                mensaje_personalizado = f"Estimado(a) {inv['nombre']}:\n\n{mensaje}"
                                
                                if enviar_correo(inv['email'], asunto, mensaje_personalizado):
                                    exitosos += 1
                                
                                progress.progress((i + 1) / total)
                                time.sleep(pausa)
                                
                                if (i + 1) % grupo == 0 and (i + 1) < total:
                                    status.text(f"⏸️ Pausa {pausa_grupo}s...")
                                    time.sleep(pausa_grupo)
                            
                            progress.empty()
                            status.empty()
                            
                            if exitosos > 0:
                                st.success(f"✅ {exitosos}/{total} correos enviados")
                                registrar_envio_log(conv['id'], conv['titulo'], total, exitosos)
                                st.balloons()
                            else:
                                st.error("❌ No se enviaron correos")
    
    with tab3:
        st.header("Estadísticas y Historial")
        mostrar_historial()

if __name__ == "__main__":
    main()
