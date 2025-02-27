import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import os
import glob
import re
from scipy.interpolate import interp1d
import warnings
import unicodedata
import json

warnings.simplefilter(action='ignore', category=Warning)

# Configuración del tipo de muestreo
TIPO_MUESTREO = "CADA_HORA"
#TIPO_MUESTREO = "CADA_15_MIN"  # Opciones: "CADA_HORA" o "CADA_15_MIN" 
MINUTOS_MUESTREO = 5  # Solo se usa cuando TIPO_MUESTREO es "CADA_15_MIN"

def log_mensaje(mensaje, critical=False):
    if critical:
        print(mensaje)

def normalizar_texto(texto):
    """
    Normaliza el texto removiendo tildes y convirtiendo a minúsculas.
    También maneja equivalencias español-inglés.
    """
    if pd.isna(texto):
        return None
        
    # Mapeo de términos inglés-español
    equivalencias = {
        'project': 'proyecto',
        'location': 'localizacion',
        'data source': 'fuente de datos',
        'geolocation': 'geolocalizacion',
        'interval': 'intervalo',
        'movement': 'movimiento'
    }
    
    # Normalizar texto (eliminar tildes y convertir a minúsculas)
    texto_normalizado = ''.join(
        c for c in unicodedata.normalize('NFD', str(texto))
        if unicodedata.category(c) != 'Mn'
    ).lower().strip()
    
    # Aplicar equivalencias si existe
    return equivalencias.get(texto_normalizado, texto_normalizado)

def normalizar_columnas(df):
    """
    Normaliza los nombres de las columnas del DataFrame
    """
    # Crear un diccionario de mapeo para las columnas
    columnas_normalizadas = {
        col: normalizar_texto(col)
        for col in df.columns
    }
    
    # Renombrar las columnas
    return df.rename(columns=columnas_normalizadas)

def corregir_intervalos(grupo, fechas_mapping):
    """
    Corrige los intervalos usando el mapeo de fechas
    """
    try:
        # Debug: Mostrar información del grupo
        print("\nDebug corregir_intervalos:")
        print(f"Tipo de grupo.name: {type(grupo.name)}")
        print(f"Contenido de grupo.name: {grupo.name}")
        
        fuente = grupo.name[0]  # FUENTE DE DATOS
        
        # Debug: Mostrar información del punto que no se encuentra
        print(f"\nFuente a buscar: {fuente}")
        print(f"Tipo de fuente: {type(fuente)}")
        print("Claves disponibles en el mapeo:")
        for k in fechas_mapping.keys():
            print(f"- {k} (tipo: {type(k)})")
        
        if fuente not in fechas_mapping:
            raise ValueError(f"FUENTE DE DATOS '{fuente}' no está en el mapeo de fechas de inicio.")
        
        # Obtener la fecha del intervalo original para mantener el día correcto
        fecha_original = pd.to_datetime(grupo['intervalo'].iloc[0].split(' - ')[0])
        fecha_inicio = fechas_mapping[fuente]  # Ya no necesitamos la tupla
        
        # Ajustar fecha_inicio para usar el día correcto del intervalo original
        fecha_inicio = fecha_inicio.replace(
            year=fecha_original.year,
            month=fecha_original.month,
            day=fecha_original.day
        )
        
        intervalos = []
        current_start = fecha_inicio
        for idx, row in grupo.iterrows():
            start = current_start
            end = start + timedelta(minutes=5)
            intervalo_str = f"{start.strftime('%m/%d/%Y %H:%M:%S')} - {end.strftime('%m/%d/%Y %H:%M:%S')}"
            intervalos.append(intervalo_str)
            current_start = end
        
        grupo['intervalo'] = intervalos
        return grupo
        
    except Exception as e:
        print(f"Error en corregir_intervalos: {str(e)}")
        print("Traceback completo:")
        import traceback
        traceback.print_exc()
        raise

def ajustar_hora_cercana(tiempo):
    """
    Ajusta el tiempo al cuarto de hora más cercano
    """
    minuto = tiempo.minute
    if minuto < 15:
        return tiempo.replace(minute=0, second=0, microsecond=0)
    elif minuto < 30:
        return tiempo.replace(minute=15, second=0, microsecond=0)
    elif minuto < 45:
        return tiempo.replace(minute=30, second=0, microsecond=0)
    else:
        return tiempo.replace(minute=45, second=0, microsecond=0)

def interpolar_datos(df):
    """
    Procesa los datos según el tipo de muestreo configurado.
    CADA_HORA: Interpola datos de 5 minutos cada hora
    CADA_15_MIN: Ajusta datos de X minutos cada 15 minutos
    """
    if TIPO_MUESTREO == "CADA_HORA":
        return interpolar_datos_horarios(df)
    else:
        return interpolar_datos_15min(df)

def interpolar_datos_horarios(df):
    """
    Interpola datos entre intervalos de 5 minutos consecutivos
    """
    resultados = []
    
    # Encontrar la columna TRICYCLE
    tricycle_cols = [col for col in df.columns if 'tricycle' in col]
    if not tricycle_cols:
        raise ValueError("No se encontró la columna TRICYCLE")
    tricycle_col = tricycle_cols[0]
    
    # Multiplicar toda la columna TRICYCLE por 3 al inicio
    df[tricycle_col] = df[tricycle_col] * 3
    
    # Agrupar por FUENTE DE DATOS y MOVIMIENTO
    for (fuente, movimiento), grupo in df.groupby(['fuente de datos', 'movimiento']):
        # Ordenar por intervalo
        grupo = grupo.sort_values('intervalo')
        
        # Obtener la fecha base
        tiempo_str = grupo['intervalo'].iloc[0].split(' - ')[0]
        tiempo_base = datetime.strptime(tiempo_str, '%m/%d/%Y %H:%M:%S')
        fecha_base = tiempo_base.replace(hour=0, minute=0, second=0)
        
        # Crear intervalos completos de 15 minutos para las 24 horas
        intervalos_completos = pd.date_range(
            start=fecha_base,
            end=fecha_base + timedelta(days=1),
            freq='15min'
        )[:-1]  # Excluir el último que sería del día siguiente
        
        # Mapear los valores originales a sus horas correspondientes
        valores_por_hora = {}
        for i, row in grupo.iterrows():
            hora_real = (i % len(grupo))
            tiempo = fecha_base + timedelta(hours=hora_real)
            valores_por_hora[tiempo] = row[tricycle_col]
        
        # Generar resultados para cada intervalo de 15 minutos
        for t in intervalos_completos:
            hora_actual = t.replace(minute=0, second=0)
            hora_siguiente = hora_actual + timedelta(hours=1)
            
            valor_actual = valores_por_hora.get(hora_actual, 0)
            valor_siguiente = valores_por_hora.get(hora_siguiente, valor_actual)  # Si no hay siguiente, mantener el actual
            
            # Calcular la interpolación basada en la posición en la hora
            minuto = t.minute
            if minuto == 0:
                valor = valor_actual
            else:
                # Interpolar entre valor_actual y valor_siguiente
                progreso = minuto / 60  # Qué tanto hemos avanzado en la hora (0.25, 0.5, 0.75)
                valor = int(round(valor_actual + (valor_siguiente - valor_actual) * progreso))
            
            resultados.append({
                'PROYECTO': grupo['proyecto'].iloc[0],
                'LOCALIZACIÓN': grupo['localizacion'].iloc[0],
                'FUENTE DE DATOS': fuente,
                'GEOLOCALIZACIÓN': grupo['geolocalizacion'].iloc[0],
                'INTERVALO': f"{t.strftime('%m/%d/%Y %H:%M:%S')} - {(t + timedelta(minutes=15)).strftime('%m/%d/%Y %H:%M:%S')}",
                'MOVIMIENTO': movimiento,
                'TRICYCLE': max(0, valor)
            })
    
    return pd.DataFrame(resultados).sort_values(['FUENTE DE DATOS', 'MOVIMIENTO', 'INTERVALO'])

def interpolar_datos_15min(df):
    """
    Nuevo método: Ajusta datos de X minutos cada 15 minutos
    """
    resultados = []
    
    # Encontrar la columna TRICYCLE
    tricycle_cols = [col for col in df.columns if 'tricycle' in col]
    if not tricycle_cols:
        raise ValueError("No se encontró la columna TRICYCLE")
    tricycle_col = tricycle_cols[0]
    
    # Calcular factor de multiplicación (15 minutos / minutos de muestreo)
    factor_multiplicacion = 15 / MINUTOS_MUESTREO
    
    # Multiplicar por el factor correspondiente y redondear al entero más cercano
    df[tricycle_col] = (df[tricycle_col] * factor_multiplicacion).round().astype(int)
    
    # Agrupar por FUENTE DE DATOS y MOVIMIENTO
    for (fuente, movimiento), grupo in df.groupby(['fuente de datos', 'movimiento']):
        # Ordenar por intervalo
        grupo = grupo.sort_values('intervalo')
        
        # Obtener la fecha base
        tiempo_str = grupo['intervalo'].iloc[0].split(' - ')[0]
        tiempo_base = datetime.strptime(tiempo_str, '%m/%d/%Y %H:%M:%S')
        fecha_base = tiempo_base.replace(hour=0, minute=0, second=0)
        
        # Procesar cada registro ajustando los intervalos
        for idx, row in grupo.iterrows():
            indice_muestra = idx % len(grupo)
            minutos_totales = (indice_muestra * 15)
            hora = minutos_totales // 60
            minuto = minutos_totales % 60
            
            tiempo_inicio = fecha_base + timedelta(hours=hora, minutes=minuto)
            tiempo_fin = tiempo_inicio + timedelta(minutes=15)
            
            resultados.append({
                'PROYECTO': row['proyecto'],
                'LOCALIZACIÓN': row['localizacion'],
                'FUENTE DE DATOS': fuente,
                'GEOLOCALIZACIÓN': row['geolocalizacion'],
                'INTERVALO': f"{tiempo_inicio.strftime('%m/%d/%Y %H:%M:%S')} - {tiempo_fin.strftime('%m/%d/%Y %H:%M:%S')}",
                'MOVIMIENTO': movimiento,
                'TRICYCLE': row[tricycle_col]
            })
    
    return pd.DataFrame(resultados).sort_values(['FUENTE DE DATOS', 'MOVIMIENTO', 'INTERVALO'])

def cargar_configuracion_fechas(archivo_config):
    """
    Carga la configuración de fechas desde un archivo Excel.
    """
    try:
        df_config = pd.read_excel(archivo_config, sheet_name=2)
        df_config.columns = [normalizar_texto(col) for col in df_config.columns]
        
        # Debug: Mostrar todos los puntos de control en la configuración
        print("\nPuntos de control en la plantilla:")
        print(df_config['punto_control'].tolist())
        
        # Convertir la columna de fecha_hora a datetime
        df_config['fecha_hora'] = pd.to_datetime(df_config['fecha_hora'])
        df_config['fecha'] = df_config['fecha_hora'].dt.date
        
        # Crear diccionario de mapeo
        config_dict = {}
        for _, row in df_config.iterrows():
            # Usar el punto de control directamente como clave
            config_dict[row['punto_control']] = row['fecha_hora']
        
        # Debug: Mostrar todas las claves del diccionario
        print("\nMapeos disponibles:")
        for pc, fecha in config_dict.items():
            print(f"PC: {pc}, Fecha: {fecha}")
            
        return config_dict
        
    except Exception as e:
        print(f"Error al cargar el archivo de configuración: {str(e)}")
        raise

def procesar_archivo_inicial(archivo_excel, fecha_inicio_mapping):
    """
    Procesa el archivo inicial usando el mapeo directo de puntos de control a fechas
    """
    try:
        # Extraer información de la carpeta padre
        carpeta_padre = os.path.basename(os.path.dirname(archivo_excel))
        numero_dia = obtener_numero_dia(carpeta_padre)
        
        if not numero_dia:
            print(f"No se pudo extraer el número de día de la carpeta: {carpeta_padre}")
            return None, None
        
        print(f"\nProcesando archivo: {os.path.basename(archivo_excel)}")
        print(f"Número de día: {numero_dia}")

        # Leer el archivo Excel usando la segunda hoja
        df = pd.read_excel(archivo_excel, sheet_name=1, skiprows=1)
        df = normalizar_columnas(df)

        # Verificar columnas necesarias (en ambos idiomas)
        required_columns = [
            ('fuente de datos', 'data source'),
            ('movimiento', 'movement'),
            ('intervalo', 'interval'),
            ('localizacion', 'location'),
            ('proyecto', 'project')  # Añadir proyecto/project a las columnas requeridas
        ]
        
        for cols in required_columns:
            if not any(col in df.columns for col in cols):
                raise ValueError(f"No se encuentra ninguna de las columnas: {cols}")
        
        # Normalizar nombres de columnas al español
        column_mapping = {
            'data source': 'fuente de datos',
            'movement': 'movimiento',
            'interval': 'intervalo',
            'location': 'localizacion',
            'project': 'proyecto',
            'geolocation': 'geolocalizacion'
        }
        
        # Aplicar el mapeo de columnas
        for eng_col, esp_col in column_mapping.items():
            if eng_col in df.columns:
                df[esp_col] = df[eng_col]
                df = df.drop(columns=[eng_col])

        # Asegurarse de que todas las columnas necesarias estén presentes
        columnas_requeridas = ['proyecto', 'localizacion', 'fuente de datos', 
                              'geolocalizacion', 'intervalo', 'movimiento']
        for col in columnas_requeridas:
            if col not in df.columns:
                raise ValueError(f"Columna requerida '{col}' no encontrada después de la normalización")

        # Extraer fecha del campo LOCALIZACIÓN
        fecha_str = df['localizacion'].iloc[0]
        match = re.search(r'(\d{2}\.\d{2}\.\d{4})', fecha_str)
        if not match:
            match = re.search(r'(\d{2}\.\d{2})', fecha_str)
        if not match:
            raise ValueError(f"No se pudo extraer la fecha de LOCALIZACIÓN: {fecha_str}")
        
        # Extraer nombre del día
        match_dia = re.search(r'Dia \d+\s*-\s*(.*?)\s+\d{2}', fecha_str)
        if not match_dia:
            match_dia = re.search(r'Dia \d+\s*-\s*(.*?)$', fecha_str)
        nombre_dia = match_dia.group(1).strip() if match_dia else 'dia'

        # Limpiar espacios en blanco de la columna fuente de datos
        df['fuente de datos'] = df['fuente de datos'].str.strip()
        
        # Debug: Mostrar valores después de limpiar
        print("\nValores únicos en FUENTE DE DATOS después de limpiar:")
        print(df['fuente de datos'].unique())
        
        # Obtener la fecha del primer intervalo del archivo
        primera_fecha = pd.to_datetime(df['intervalo'].iloc[0].split(' - ')[0])
        fecha = primera_fecha.strftime('%d-%m')  # Convertir a string DD-MM
        
        print("\nDEBUG FECHA PASO A PASO:")
        print(f"1. Fecha después de strftime: {fecha}")
        
        # Crear un nuevo diccionario de mapeo específico para esta fecha
        mapeo_fecha_especifica = {}
        for punto_control, fecha_hora in fecha_inicio_mapping.items():
            if punto_control.strip() in df['fuente de datos'].unique():
                mapeo_fecha_especifica[punto_control.strip()] = fecha_hora
        
        print(f"2. Fecha después del mapeo: {fecha}")
        
        # Procesar y corregir los intervalos
        df_corrected = df.groupby(['fuente de datos', 'movimiento'], group_keys=False).apply(
            lambda x: corregir_intervalos(x, mapeo_fecha_especifica)
        )
        
        print(f"3. Fecha después de corregir_intervalos: {fecha}")
        
        return df_corrected, (numero_dia, nombre_dia, fecha)
        
    except Exception as e:
        print(f"Error en día {numero_dia}: {str(e)}")
        print(f"Traceback completo:")
        import traceback
        traceback.print_exc()
        return None, None

def main():
    # Cargar configuración si existe
    try:
        with open('config.json', 'r') as f:
            config = json.load(f)
            TIPO_MUESTREO = config.get('TIPO_MUESTREO', 'CADA_HORA')
            MINUTOS_MUESTREO = config.get('MINUTOS_MUESTREO', 5)
    except:
        # Usar valores por defecto si no hay configuración
        TIPO_MUESTREO = "CADA_HORA"
        MINUTOS_MUESTREO = 5
    
    print("\n" + "="*50)
    print("Iniciando procesamiento de datos de Filipinas")
    print("="*50 + "\n")
    
    # Crear carpetas de salida
    carpeta_final = 'data_filipinas'
    os.makedirs(carpeta_final, exist_ok=True)
    
    # Cargar configuración desde archivo Excel
    archivo_config = 'Plantilla/plantilla_peru.xlsx'
    try:
        fecha_inicio_mapping = cargar_configuracion_fechas(archivo_config)
    except Exception as e:
        log_mensaje(f"Error fatal al cargar la configuración: {str(e)}", critical=True)
        return
    
    # Buscar carpetas numeradas
    carpeta_base = 'Multisource Categoría Filipinas'
    carpetas_dias = []
    
    for item in os.listdir(carpeta_base):
        ruta_completa = os.path.join(carpeta_base, item)
        if os.path.isdir(ruta_completa):
            if obtener_numero_dia(item) is not None:
                carpetas_dias.append((obtener_numero_dia(item), ruta_completa))
    
    # Ordenar carpetas por número
    carpetas_dias.sort(key=lambda x: x[0])
    
    # Procesar cada carpeta
    for numero_dia, carpeta_dia in carpetas_dias:
        archivos_excel = glob.glob(os.path.join(carpeta_dia, '*.xlsx'))
        
        for archivo in archivos_excel:
            try:
                resultado = procesar_archivo_inicial(archivo, fecha_inicio_mapping)
                if resultado[0] is not None:
                    df_procesado, (numero_dia, nombre_dia, fecha) = resultado
                    
                    # Debug del nombre de archivo
                    print("\nDEBUG NOMBRE ARCHIVO:")
                    print(f"Número día: {numero_dia}")
                    print(f"Nombre día: {nombre_dia}")
                    print(f"Fecha: {fecha}")
                    
                    nombre_archivo = f'{numero_dia}.{nombre_dia}_{fecha}_filipinas.xlsx'
                    print(f"Nombre archivo final: {nombre_archivo}")
                    
                    archivo_salida = os.path.join(carpeta_final, nombre_archivo)
                    df_interpolado = interpolar_datos(df_procesado)
                    df_interpolado.to_excel(archivo_salida, index=False)
                    log_mensaje(f"Archivo guardado: {os.path.basename(archivo_salida)}\n", critical=True)
                    
                    # PUNTO DE DEBUG 2
                    print("\nDEBUG FECHA EN MAIN:")
                    print(f"fecha después de desempaquetar: {fecha}")
                    print(f"tipo de fecha: {type(fecha)}")
                    
            except Exception as e:
                log_mensaje(f"Error en día {numero_dia}: {str(e)}\n", critical=True)
                continue

def obtener_numero_dia(nombre_carpeta):
    """
    Extrae el número del día del nombre de la carpeta
    Solo considera el número inicial
    """
    match = re.match(r'(\d+)', nombre_carpeta)
    return int(match.group(1)) if match else None

def ajustar_intervalos(df):
    """
    Ajusta los intervalos de 5 minutos a sus horas reales
    """
    intervalos_ajustados = []
    for i, row in df.iterrows():
        hora = i // 12  # Cada 12 registros de 5 min = 1 hora
        tiempo_inicio = datetime(2025, 2, 8, hora, 0)  # La fecha es irrelevante
        tiempo_fin = tiempo_inicio + timedelta(minutes=5)
        intervalos_ajustados.append(f"{tiempo_inicio.strftime('%H:%M:%S')} - {tiempo_fin.strftime('%H:%M:%S')}")
    
    df['intervalo'] = intervalos_ajustados
    return df

def interpolar_15min(df):
    """
    Interpola los datos a intervalos de 15 minutos
    """
    # Aquí la lógica de interpolación

if __name__ == "__main__":
    main()