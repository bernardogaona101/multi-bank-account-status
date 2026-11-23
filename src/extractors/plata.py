import re
import pandas as pd
from src.utils import clean_global_date


# Función para obtener metadatos (Cuenta y Fecha de Corte)
def get_metadata_pdf(pages_text):
    clabe = "CLABE_Unknown"
    fecha_corte = "Date_Unknown"

    for text in pages_text[:2]:
        match_clabe = re.search(r'Número de cuenta[:\s]*(\d{10})', text, re.IGNORECASE)
        if match_clabe and clabe == "CLABE_Unknown":
            clabe = match_clabe.group(1)

        match_fecha = re.search(r'Fecha de corte[:\s]*(\d{2}-[a-z]{3}-\d{4})', text, re.IGNORECASE)
        if match_fecha and fecha_corte == "Date_Unknown":
            fecha_corte = clean_global_date(match_fecha.group(1))

        if clabe != "CLABE_Unknown" and fecha_corte != "Date_Unknown":
            break

    return clabe, fecha_corte


# Función para extraer compras a Meses Sin Intereses (MSI)
def extract_msi_rec(pages_text):
    # En el nuevo formato, el encabezado superior es 'Desglose de movimientos'
    start_key = 'Desglose de movimientos'
    end_key = 'Cargos, abonos y compras regulares'

    msi_record_pattern = re.compile(
        r'(\d{2}-[a-z]{3}-\d{4})\s+'        # 1. Fecha operación
        r'(.*?)\s+'                         # 2. Descripción
        r'\$([\d,]+\.\d{2})\s+'             # 3. Monto original
        r'\$([\d,]+\.\d{2})\s+'             # 4. Saldo pendiente
        r'\$([\d,]+\.\d{2})\s+'             # 5. Pago requerido
        r'(\d+\s+de\s+\d+)\s+'              # 6. Núm. de pago
        r'([\d\.]+%)',                      # 7. Tasa de interés
        re.DOTALL | re.IGNORECASE
    )

    records_found = []
    all_text = "\n".join(pages_text)

    clean_block = ""
    in_table = False

    for line in all_text.split('\n'):
        clean_line = line.strip()

        if start_key in clean_line and not in_table:
            in_table = True
            continue

        if end_key in clean_line and in_table:
            in_table = False
            break

        if in_table:
            if not clean_line.startswith("Notas: Ver notas") and \
               not clean_line.startswith("Número de cuenta:") and \
               not clean_line.startswith("Tarjeta titular") and \
               not clean_line.startswith("Tarjeta adicional") and \
               "Compras y cargos diferidos a meses" not in clean_line:
                clean_block += clean_line + "\n"

    for match in msi_record_pattern.finditer(clean_block):
        date = match.group(1)

        description = match.group(2).replace('\n', ' ').strip()
        description = re.sub(r'\s+', ' ', description)

        original_amount = match.group(3).replace(',', '')
        pending_balance = match.group(4).replace(',', '')
        payment_required = match.group(5).replace(',', '')

        num_pay = match.group(6)
        interest_rate = match.group(7)

        records_found.append({
            'Fecha': date,
            'Descripcion': description,
            'Monto_Original': float(original_amount),
            'Saldo_Pendiente': float(pending_balance),
            'Pago_Mensual': float(payment_required),
            'Num_Pago': num_pay,
            'Tasa_Interes': interest_rate
        })

    if records_found:
        return pd.DataFrame(records_found)
    return None


# Función para extraer movimientos regulares (Titular y Adicional)
def extract_regular_rec(pages_text):
    start_key = 'abonos y compras regulares (no a meses)'
    end_key = 'Total cargos'

    # Adaptado: sin número de tarjeta en la fila y con signos (+ / -) opcionales
    pattern_plata = re.compile(
        r'(\d{2}-[a-z]{3}-\d{4})\s+'         # 1. Fecha operación
        r'(\d{2}-[a-z]{3}-\d{4})\s+'         # 2. Fecha cargo
        r'(.*?)\s+'                          # 3. Descripción
        r'([+\-−]?)\s*([\d,]+\.\d{2})\s+'    # 4. Signo opcional y 5. Monto original
        r'([A-Z]{3})\s+'                     # 6. Moneda
        r'([\d\.]+)\s+'                      # 7. Tipo de cambio
        r'([+\-−]?)\s*\$([\d,]+\.\d{2})',    # 8. Signo opcional y 9. Monto MXN final
        re.IGNORECASE | re.DOTALL
    )

    records_found = []
    all_text = "\n".join(pages_text)

    clean_block = ""
    in_table = False

    for line in all_text.split('\n'):
        clean_line = line.strip()

        if start_key in clean_line and not in_table:
            in_table = True
            continue

        if end_key in clean_line and in_table:
            in_table = False
            break

        if in_table:
            if not clean_line.startswith("Notas: Ver notas") and \
               not clean_line.startswith("Número de cuenta:") and \
               not clean_line.startswith("Tarjeta titular") and \
               not clean_line.startswith("Tarjeta adicional") and \
               start_key not in clean_line:
                clean_block += clean_line + "\n"

    for match in pattern_plata.finditer(clean_block):
        fecha_operacion = match.group(1)
        fecha_cargo = match.group(2)

        descripcion = match.group(3).replace('\n', ' ').strip()
        descripcion = re.sub(r'\s+', ' ', descripcion)

        signo_1 = match.group(4).strip()
        signo_2 = match.group(8).strip()
        raw_amount = match.group(9).replace(',', '')
        amount = float(raw_amount)

        # Convertir pagos/abonos a negativo para que transform.py los excluya del gasto
        es_abono = (
            signo_1 in ['-', '−'] or
            signo_2 in ['-', '−'] or
            descripcion.lower().startswith((
                'pago ', 'abono ', 'devolucion', 'devolución',
                'bonificacion', 'bonificación', 'cashback'
            ))
        )
        if es_abono:
            amount = -abs(amount)

        records_found.append({
            'Fecha_Operacion': fecha_operacion,
            'Fecha_Cargo': fecha_cargo,
            'Descripcion': descripcion,
            'Monto': amount,
            'Tipo_Movimiento': 'REGULAR'
        })

    if records_found:
        return pd.DataFrame(records_found)
    return None


# Función orquestadora del extractor Plata
def extract_plata(pages_text):
    clabe, fecha = get_metadata_pdf(pages_text)
    df_msi = extract_msi_rec(pages_text)
    df_regular = extract_regular_rec(pages_text)
    return clabe, fecha, df_msi, df_regular