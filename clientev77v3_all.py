# Código Completo: clientev77_IA_debug.py
# Versão 50.4: CORREÇÃO DE DEBUG E GLOBAL DATA APLICADA.
# - Implementação de CONFIG_LOCK para proteger RUN_ID e CFG.
# - Uso de STORE_LOCK estendido para COM_LOG e ULTIMA_RESPOSTA.
# - Garantia de thread-safety para todas as variáveis globais modificadas.

import json, os, io, time, threading, traceback, socket, asyncio
from datetime import datetime
from typing import Dict, Any, List
from flask import Flask, jsonify, request, send_file, render_template_string
import requests
from fpdf import FPDF  # fpdf2

# ---- Modbus ----
from pymodbus.client.sync import ModbusTcpClient as PymodbusTcpClient
from pymodbus.client.sync import ModbusSerialClient
from pyModbusTCP.client import ModbusClient as PyModbusTCPClient
# ---- MQTT / OPC UA ----
from paho.mqtt import client as mqtt
from opcua import Client as OPCUAClient

# ---- Bluetooth (BLE) optional ----
try:
    from bleak import BleakClient
except Exception:
    BleakClient = None

# ---- LoRa (Serial) optional ----
try:
    import serial
except Exception:
    serial = None


APP = Flask(__name__)
CFG_PATH = "config.json"
DEBUG_LOG = True
RUN_ID = 0  # incrementado a cada mudança de config

# Locks para Thread Safety
STORE_LOCK = threading.Lock()   # Protege STORE, HIST, COM_LOG e ULTIMA_RESPOSTA
CONFIG_LOCK = threading.Lock()  # Protege CFG e RUN_ID

STORE: Dict[str, Dict[str, Any]] = {}
HIST: List[Dict[str, Any]] = []
COM_LOG: List[str] = []
ULTIMA_RESPOSTA = ""

# ====================== CONFIG ======================

DEFAULT_CFG = {
    "refresh_ms": 1000,
    "active_protocol": "modbus_tcp",  # modbus_tcp | modbus_rtu | mqtt | opcua | wifi | bluetooth | lora | lorawan
    "ia": {
        "provider": "groq",
        "api_key": "",
        "model": "llama-3.1-8b-instant",
        "endpoint_chat": "https://api.groq.com/openai/v1/chat/completions",
        "endpoint_models": "https://api.groq.com/openai/v1/models"
    },
    "modbus_tcp": {
        "backend": "pyModbusTCP",   # "pyModbusTCP" (padrão) | "pymodbus"
        "host": "127.0.0.1",
        "port": 5020,
        "unit_id": 1,
        "tags": [
            {"label":"tensão","addr":0,"fc":"hr","count":1,"scale":0.1,"unit":"V","max":300,"color":"#f6c431","decimals":2},
            {"label":"corrente","addr":1,"fc":"hr","count":1,"scale":0.01,"unit":"A","max":50,"color":"#1e88e5","decimals":2},
            {"label":"fp","addr":2,"fc":"hr","count":1,"scale":0.01,"unit":"","max":1,"color":"#7e7e7e","decimals":2},
            {"label":"temperatura","addr":3,"fc":"hr","count":1,"scale":1.0,"unit":"°C","max":120,"color":"#e53935","decimals":2},
            {"label":"vazão","addr":4,"fc":"hr","count":1,"scale":0.1,"unit":"L/min","max":100,"color":"#43a047","decimals":2},
            {"label":"nível","addr":5,"fc":"hr","count":1,"scale":0.1,"unit":"%","max":100,"color":"#3949ab","decimals":2},
            {"label":"ph","addr":6,"fc":"hr","count":1,"scale":0.01,"unit":"","max":14,"color":"#8e24aa","decimals":2},
            {"label":"umidade","addr":7,"fc":"hr","count":1,"scale":0.1,"unit":"%","max":100,"color":"#00897b","decimals":2}
        ]
    },
    "modbus_rtu": {
        "method": "rtu",
        "port": "/dev/ttyUSB0",
        "baudrate": 9600,
        "parity": "N",
        "stopbits": 1,
        "bytesize": 8,
        "timeout": 1,
        "unit_id": 1,
        "tags": [
            {"label":"tensão","addr":0,"fc":"hr","count":1,"scale":0.1,"unit":"V","max":300,"color":"#f6c431","decimals":2},
            {"label":"corrente","addr":1,"fc":"hr","count":1,"scale":0.01,"unit":"A","max":50,"color":"#1e88e5","decimals":2},
        ]
    },
    "mqtt": {
        "broker": "127.0.0.1",
        "port": 1883,
        "topics": [
            {"label":"tensão","topic":"planta/sensor/tensão","unit":"V","max":300,"color":"#f6c431","decimals":2},
            {"label":"corrente","topic":"planta/sensor/corrente","unit":"A","max":50,"color":"#1e88e5","decimals":2},
        ]
    },
    "opcua": {
        "endpoint": "opc.tcp://127.0.0.1:4840/freeopcua/server/",
        "nodes": [
            {"label":"nível","nodeid":"ns=2;i=3","unit":"%","max":100,"color":"#3949ab","decimals":2},
            {"label":"pressao","nodeid":"ns=2;i=2","unit":"psi","max":200,"color":"#6d4c41","decimals":2},
        ]
    },
    "wifi": {
        "host": "192.168.4.1",
        "port": 12345,
        "tags": [
            {"label": "sensor1", "unit": "u", "max": 100, "color": "#009688", "decimals": 2},
            {"label": "sensor2", "unit": "u", "max": 50,  "color": "#FF5722", "decimals": 2},
        ]
    },
    "bluetooth": {
        "address": "00:00:00:00:00:00",
        "uuid": "00001101-0000-1000-8000-00805F9B34FB",
        "tags": [
            {"label": "temp", "unit": "°C", "max": 100, "color": "#3F51B5", "decimals": 2},
        ]
    },
    "lora": {
        "port": "/dev/ttyUSB1",
        "baudrate": 115200,
        "timeout": 2,
        "tags": [
            {"label": "node1_temp", "unit": "°C", "max": 80, "color": "#e53935", "decimals": 2},
            {"label": "node1_hum", "unit": "%", "max": 100, "color": "#1e88e5", "decimals": 2}
        ]
    },
    "lorawan": {
        "broker": "eu1.cloud.thethings.network",
        "port": 1883,
        "username": "meu-app@ttn",
        "password": "NNS....",
        "device_id": "eui-aabbccddeeff0011",
        "topics": [
            {"label": "temperatura_wan", "topic": "v3/+/devices/+/up", "json_key": "uplink_message.decoded_payload.temperature", "unit": "°C", "max": 80, "color": "#e53935", "decimals": 2},
            {"label": "umidade_wan", "topic": "v3/+/devices/+/up", "json_key": "uplink_message.decoded_payload.humidity", "unit": "%", "max": 100, "color": "#1e88e5", "decimals": 2}
        ]
    }
}

def load_cfg() -> dict:
    if not os.path.exists(CFG_PATH):
        save_cfg(DEFAULT_CFG)
        return json.loads(json.dumps(DEFAULT_CFG))
    with open(CFG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)

def save_cfg(cfg: dict):
    with open(CFG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)

CFG = load_cfg()

# ====================== UTIL ======================

def log(msg: str):
    if DEBUG_LOG:
        print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")

def log_com_event(protocolo: str, status: str, msg: str):
    """Registra um evento de comunicação (perda/restabelecimento) de forma segura."""
    agora = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    linha = f"[{agora}] | {protocolo.upper()} | {status.upper()}: {msg}"
    
    with STORE_LOCK: # Proteção de thread para COM_LOG
        COM_LOG.append(linha)
        if len(COM_LOG) > 300: # Mantém os últimos 300 registros
            COM_LOG.pop(0)
            
    log(f"COM_EVENT: {linha}") # Também envia para o log principal

def set_value(label, value, unit, src, status="OK", err=None):
    with STORE_LOCK:
        STORE[label] = {
            "value": value, "unit": unit, "src": src, "status": status,
            "error": err, "ts": datetime.now().isoformat(timespec="seconds")
        }

def set_error(label, unit, src, err):
    set_value(label, None, unit, src, status="ERR", err=err)

def hist_push(snapshot: Dict[str, Any]):
    with STORE_LOCK:
        HIST.append(snapshot)
        if len(HIST) > 300: # Mantém os últimos 300 registros
            HIST.pop(0)

def get_decimals_for_label(label: str) -> int:
    # Não precisa de lock, CFG é lido, não modificado aqui.
    ap = CFG.get("active_protocol")
    items = []
    if ap == "modbus_tcp": items = CFG["modbus_tcp"].get("tags", [])
    elif ap == "modbus_rtu": items = CFG["modbus_rtu"].get("tags", [])
    elif ap == "mqtt": items = CFG["mqtt"].get("topics", [])
    elif ap == "opcua": items = CFG["opcua"].get("nodes", [])
    elif ap == "wifi": items = CFG["wifi"].get("tags", [])
    elif ap == "bluetooth": items = CFG["bluetooth"].get("tags", [])
    elif ap == "lora": items = CFG["lora"].get("tags", [])
    elif ap == "lorawan": items = CFG["lorawan"].get("topics", [])
    for t in items:
        if t.get("label") == label:
            return int(t.get("decimals", 2))
    return 2

# ====================== RUNNER (RUN_ID) ======================

def start_workers():
    """Inicia o worker do protocolo ativo e invalida os anteriores."""
    global RUN_ID
    
    with CONFIG_LOCK: # Proteção de thread para RUN_ID e leitura de CFG
        RUN_ID += 1
        my_id = RUN_ID
        ap = CFG.get("active_protocol")
        
    log(f"start_workers -> run_id={my_id}, protocol={ap}")

    def guard(fn):
        def _run():
            fn(my_id)
        threading.Thread(target=_run, daemon=True).start()

    if ap == "modbus_tcp": guard(worker_modbus_tcp)
    elif ap == "modbus_rtu": guard(worker_modbus_rtu)
    elif ap == "mqtt": guard(worker_mqtt)
    elif ap == "opcua": guard(worker_opcua)
    elif ap == "wifi": guard(worker_wifi)
    elif ap == "bluetooth": guard(worker_bluetooth)
    elif ap == "lora": guard(worker_lora)
    elif ap == "lorawan": guard(worker_lorawan)


def still_alive(my_id): 
    with CONFIG_LOCK:
        return my_id == RUN_ID

# ====================== WORKERS ======================

def worker_modbus_tcp(my_id):
    # CFG é copiado implicitamente para a thread no início, mas o loop depende de still_alive(my_id)
    c = CFG["modbus_tcp"]; host=c["host"]; port=int(c["port"]); unit=int(c["unit_id"])
    backend = (c.get("backend") or "pyModbusTCP").strip()
    interval = max(0.1, CFG.get("refresh_ms",1000)/1000.0)

    for t in c["tags"]:
        set_value(t["label"], None, t.get("unit",""), "modbus_tcp", "CONECTANDO")

    back = 1.0
    log(f"Worker Modbus TCP iniciado (backend={backend}, run_id={my_id})")
    while still_alive(my_id) and CFG.get("active_protocol") == "modbus_tcp":
        cli = None
        try:
            if backend == "pymodbus":
                cli = PymodbusTcpClient(host=host, port=port)
                if not cli.connect(): raise Exception("conexão (pymodbus) falhou")
                log_com_event("modbus_tcp", "Restabelecida", f"Conexão com {host}:{port} OK (pymodbus)")
                while still_alive(my_id) and CFG.get("active_protocol") == "modbus_tcp":
                    snap={"tempo": time.strftime("%H:%M:%S")}
                    for t in c["tags"]:
                        lab=t["label"]; unit_=t.get("unit",""); fc=t.get("fc","hr").lower()
                        addr=int(t.get("addr",0)); count=int(t.get("count",1)); scale=float(t.get("scale",1))
                        dec=int(t.get("decimals",2))
                        try:
                            if fc=="hr": rr=cli.read_holding_registers(addr,count,unit=unit)
                            elif fc=="ir": rr=cli.read_input_registers(addr,count,unit=unit)
                            else: raise Exception(f"FC não suportada: {fc}")
                            if rr.isError(): raise Exception(str(rr))
                            raw=rr.registers[0] if rr.registers else 0
                            val=round(raw*scale, dec)
                            set_value(lab,val,unit_,"modbus_tcp"); snap[lab]=val
                        except Exception as e:
                            set_error(lab,unit_,"modbus_tcp",f"{type(e).__name__}: {e}")
                    hist_push(snap); time.sleep(interval)
            else:
                cli = PyModbusTCPClient(host=host, port=port, auto_open=True, auto_close=False, timeout=3)
                if not cli.open(): log("Aviso: pyModbusTCP ainda não abriu; tentando ler...")
                log_com_event("modbus_tcp", "Restabelecida", f"Conexão com {host}:{port} OK (pyModbusTCP)")
                while still_alive(my_id) and CFG.get("active_protocol") == "modbus_tcp":
                    snap={"tempo": time.strftime("%H:%M:%S")}
                    for t in c["tags"]:
                        lab=t["label"]; unit_=t.get("unit",""); fc=t.get("fc","hr").lower()
                        addr=int(t.get("addr",0)); count=int(t.get("count",1)); scale=float(t.get("scale",1))
                        dec=int(t.get("decimals",2))
                        try:
                            if fc=="hr": regs=cli.read_holding_registers(addr, count)
                            elif fc=="ir": regs=cli.read_input_registers(addr, count)
                            else: raise Exception(f"FC não suportada: {fc}")
                            if not regs: raise Exception("leitura vazia/None")
                            raw=regs[0]
                            val=round(raw*scale, dec)
                            set_value(lab,val,unit_,"modbus_tcp"); snap[lab]=val
                        except Exception as e:
                            set_error(lab,unit_,"modbus_tcp",f"{type(e).__name__}: {e}")
                    hist_push(snap); time.sleep(interval)
            back = 1.0
        except Exception as e:
            err=f"ModbusTCPConnect: {e}"
            log_com_event("modbus_tcp", "Perdida", err)
            log(err); log(traceback.format_exc())
            for t in c["tags"]: set_error(t["label"], t.get("unit",""), "modbus_tcp", err)
            time.sleep(back); back=min(back*2, 10)
        finally:
            try:
                if isinstance(cli, PymodbusTcpClient) or isinstance(cli, PyModbusTCPClient):
                    cli.close()
            except Exception: pass

def worker_modbus_rtu(my_id):
    c=CFG["modbus_rtu"]
    interval=max(0.1, CFG.get("refresh_ms",1000)/1000.0)
    for t in c["tags"]:
        set_value(t["label"], None, t.get("unit",""), "modbus_rtu", "CONECTANDO")
    back=1.0
    log(f"Worker Modbus RTU iniciado (run_id={my_id})")
    while still_alive(my_id) and CFG.get("active_protocol")=="modbus_rtu":
        cli=None
        try:
            cli=ModbusSerialClient(method=c.get("method","rtu"), port=c["port"], baudrate=int(c["baudrate"]),
                                   parity=c.get("parity","N"), stopbits=int(c.get("stopbits",1)),
                                   bytesize=int(c.get("bytesize",8)), timeout=float(c.get("timeout",1)))
            if not cli.connect(): raise Exception("conexão serial falhou")
            log_com_event("modbus_rtu", "Restabelecida", f"Conexão com {c['port']} OK")
            back=1.0
            while still_alive(my_id) and CFG.get("active_protocol")=="modbus_rtu":
                snap={"tempo": time.strftime("%H:%M:%S")}
                for t in c["tags"]:
                    lab=t["label"]; unit_=t.get("unit",""); fc=t.get("fc","hr").lower()
                    addr=int(t.get("addr",0)); count=int(t.get("count",1)); scale=float(t.get("scale",1))
                    dec=int(t.get("decimals",2))
                    try:
                        if fc=="hr": rr=cli.read_holding_registers(addr,count,unit=int(c["unit_id"]))
                        elif fc=="ir": rr=cli.read_input_registers(addr,count,unit=int(c["unit_id"]))
                        else: raise Exception(f"FC não suportada: {fc}")
                        if rr.isError(): raise Exception(str(rr))
                        raw=rr.registers[0] if rr.registers else 0
                        val=round(raw*scale, dec)
                        set_value(lab,val,unit_,"modbus_rtu"); snap[lab]=val
                    except Exception as e:
                        set_error(lab,unit_,"modbus_rtu",f"{type(e).__name__}: {e}")
                hist_push(snap); time.sleep(interval)
        except Exception as e:
            err=f"ModbusRTUConnect: {e}"
            log_com_event("modbus_rtu", "Perdida", err)
            log(err); log(traceback.format_exc())
            for t in c["tags"]: set_error(t["label"], t.get("unit",""), "modbus_rtu", err)
            time.sleep(back); back=min(back*2,10)
        finally:
            try:
                if cli: cli.close()
            except: pass

def worker_mqtt(my_id):
    c=CFG["mqtt"]; broker=c["broker"]; port=int(c["port"])
    topics={t["topic"]:(t["label"], t.get("unit",""), int(t.get("decimals",2))) for t in c.get("topics",[])}
    for t in c.get("topics",[]): set_value(t["label"], None, t.get("unit",""), "mqtt", "CONECTANDO")

    cli=mqtt.Client()
    def on_connect(client,userdata,flags,rc):
        ok=(rc==0); log(f"MQTT connect rc={rc} (run_id={my_id})")
        msg = f"Conexão com broker {broker}:{port} OK" if ok else f"Falha na conexão com broker, rc={rc}"
        status = "Restabelecida" if ok else "Perdida"
        log_com_event("mqtt", status, msg)
        for topic,(lab,unit_,_) in topics.items():
            if ok: client.subscribe(topic); set_value(lab,None,unit_,"mqtt","AGUARDANDO")
            else:  set_error(lab,unit_,"mqtt",f"rc={rc}")

    def on_message(client,userdata,msg):
        if not still_alive(my_id) or CFG.get("active_protocol")!="mqtt":
            return
        try:
            raw=msg.payload.decode("utf-8").strip()
            try: v=float(raw.replace(",", "."))
            except: v=raw
            lab,unit_,dec=topics.get(msg.topic,(msg.topic,"",2))
            if isinstance(v,(int,float)): v=round(float(v), dec)
            set_value(lab,v,unit_,"mqtt")
        except Exception as e:
            lab,unit_,_ = topics.get(msg.topic,(msg.topic,"",2))
            set_error(lab,unit_,"mqtt",f"MQTTMessage: {e}")

    cli.on_connect=on_connect; cli.on_message=on_message

    back=1.0
    log(f"Worker MQTT iniciado (run_id={my_id})")
    while still_alive(my_id) and CFG.get("active_protocol")=="mqtt":
        try:
            cli.connect(broker,port,60); cli.loop_start()
            while still_alive(my_id) and CFG.get("active_protocol")=="mqtt":
                snap={"tempo": time.strftime("%H:%M:%S")}
                for t in c.get("topics", []):
                    lab=t["label"]; snap[lab]=STORE.get(lab, {}).get("value", None)
                hist_push(snap)
                time.sleep(max(0.5, CFG.get("refresh_ms",1000)/1000.0))
            cli.loop_stop(); back=1.0
        except Exception as e:
            err=f"MQTTConnect: {e}"
            log_com_event("mqtt", "Perdida", err)
            log(err); log(traceback.format_exc())
            for _,(lab,unit_,_) in topics.items(): set_error(lab,unit_,"mqtt",err)
            time.sleep(back); back=min(back*2,10)

def worker_opcua(my_id):
    c=CFG["opcua"]; endpoint=c["endpoint"]
    interval=max(0.1, CFG.get("refresh_ms",1000)/1000.0)
    for n in c.get("nodes",[]): set_value(n["label"], None, n.get("unit",""), "opcua", "CONECTANDO")
    back=1.0; cli=None
    log(f"Worker OPC UA iniciado (run_id={my_id})")
    while still_alive(my_id) and CFG.get("active_protocol")=="opcua":
        try:
            cli=OPCUAClient(endpoint); cli.connect()
            log_com_event("opcua", "Restabelecida", f"Conexão com {endpoint} OK")
            back=1.0
            nodes=[(n["label"],n["nodeid"],n.get("unit",""),int(n.get("decimals",2))) for n in c.get("nodes",[])]
            while still_alive(my_id) and CFG.get("active_protocol")=="opcua":
                snap={"tempo": time.strftime("%H:%M:%S")}
                for (lab,nodeid,unit_,dec) in nodes:
                    try:
                        v=cli.get_node(nodeid).get_value()
                        if isinstance(v,(int,float)): v=round(float(v), dec)
                        set_value(lab,v,unit_,"opcua"); snap[lab]=v
                    except Exception as e:
                        set_error(lab,unit_,"opcua",f"OPCUARead: {e}")
                hist_push(snap); time.sleep(interval)
        except Exception as e:
            err=f"OPCUAConnect: {e}"
            log_com_event("opcua", "Perdida", err)
            log(err); log(traceback.format_exc())
            for n in c.get("nodes",[]): set_error(n["label"], n.get("unit",""), "opcua", err)
            time.sleep(back); back=min(back*2,10)
        finally:
            try:
                if cli: cli.disconnect()
            except: pass

# ====================== WORKERS ADICIONAIS: WIFI, BLUETOOTH, LORA, LORAWAN ======================

def worker_wifi(my_id):
    c = CFG["wifi"]; host=c["host"]; port=int(c["port"])
    for t in c["tags"]: set_value(t["label"], None, t.get("unit",""), "wifi", "CONECTANDO")

    log(f"Worker Wi-Fi iniciado (run_id={my_id})")
    while still_alive(my_id) and CFG.get("active_protocol") == "wifi":
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(6)
                s.connect((host, port))
                s.settimeout(2)
                log_com_event("wifi", "Restabelecida", f"Conexão com {host}:{port} OK")
                buffer = b""
                while still_alive(my_id) and CFG.get("active_protocol") == "wifi":
                    try:
                        data = s.recv(1024)
                        if not data:
                            log_com_event("wifi", "Perdida", "Socket fechado pelo servidor")
                            break
                        buffer += data
                        if b'\n' in buffer:
                            lines = buffer.split(b'\n')
                            buffer = lines.pop() # Mantém a última linha parcial
                            for line in lines:
                                line = line.strip()
                                if not line: continue
                                try:
                                    j = json.loads(line.decode())
                                    snap={"tempo": time.strftime("%H:%M:%S")}
                                    for t in c["tags"]:
                                        lab=t["label"]; v=j.get(lab)
                                        if isinstance(v,(int,float)): v=round(float(v), int(t.get("decimals",2)))
                                        set_value(lab,v,t.get("unit",""),"wifi"); snap[lab]=v
                                    hist_push(snap)
                                except Exception as e:
                                    log(f"Wi-Fi linha JSON inválida: {e} -> {line}")
                        else:
                            time.sleep(0.1)
                    except socket.timeout:
                        continue
                    except Exception as e:
                        log_com_event("wifi", "Perdida", f"Erro no loop interno: {e}")
                        log(f"Wi-Fi erro loop interno: {e}"); break
        except Exception as e:
            err=f"WiFiConnect: {e}"
            log_com_event("wifi", "Perdida", err)
            log(f"Wi-Fi erro: {err}")
            for t in c["tags"]: set_error(t["label"], t.get("unit",""), "wifi", str(e))
            time.sleep(2)

def worker_bluetooth(my_id):
    if BleakClient is None:
        log("Bluetooth indisponível: instale bleak"); return
    c=CFG["bluetooth"]; addr=c["address"]; uuid=c["uuid"]
    interval=max(0.1, CFG.get("refresh_ms",1000)/1000.0)
    for t in c["tags"]: set_value(t["label"], None, t.get("unit",""), "bluetooth", "CONECTANDO")
    log(f"Worker Bluetooth iniciado (run_id={my_id})")

    async def run_loop():
        while still_alive(my_id) and CFG.get("active_protocol") == "bluetooth":
            try:
                async with BleakClient(addr) as cli:
                    log_com_event("bluetooth", "Restabelecida", f"Conexão com {addr} OK")
                    while still_alive(my_id) and CFG.get("active_protocol") == "bluetooth":
                        snap={"tempo": time.strftime("%H:%M:%S")}
                        try:
                            data = await cli.read_gatt_char(uuid)
                            if data:
                                try:
                                    j = json.loads(data.decode())
                                    for t in c["tags"]:
                                        lab=t["label"]; v=j.get(lab)
                                        if isinstance(v,(int,float)): v=round(float(v), int(t.get("decimals",2)))
                                        set_value(lab,v,t.get("unit",""),"bluetooth"); snap[lab]=v
                                except Exception as e:
                                    log(f"Bluetooth JSON inválido: {e}")
                        except Exception as e:
                            log(f"Bluetooth erro leitura: {e}")
                        hist_push(snap)
                        await asyncio.sleep(interval)
            except Exception as e:
                err=f"BluetoothConnect: {e}"
                log_com_event("bluetooth", "Perdida", err)
                log(f"Bluetooth erro conexão/loop: {err}")
                for t in c["tags"]: set_error(t["label"], t.get("unit",""), "bluetooth", str(e))
                await asyncio.sleep(2)

    loop = asyncio.new_event_loop()
    try:
        asyncio.set_event_loop(loop)
        loop.run_until_complete(run_loop())
    finally:
        try: loop.close()
        except: pass

def worker_lora(my_id):
    if serial is None:
        log("LoRa indisponível: instale pyserial"); return
    c=CFG["lora"]
    for t in c["tags"]: set_value(t["label"], None, t.get("unit",""), "lora", "CONECTANDO")
    
    log(f"Worker LoRa (Serial) iniciado (run_id={my_id})")
    while still_alive(my_id) and CFG.get("active_protocol") == "lora":
        ser = None
        try:
            ser = serial.Serial(c["port"], c["baudrate"], timeout=c.get("timeout", 2))
            log_com_event("lora", "Restabelecida", f"Porta serial {c['port']} aberta.")
            while still_alive(my_id) and CFG.get("active_protocol") == "lora":
                try:
                    line = ser.readline()
                    if line:
                        line = line.decode('utf-8').strip()
                        log(f"LoRa RX: {line}")
                        j = json.loads(line)
                        snap={"tempo": time.strftime("%H:%M:%S")}
                        for t in c["tags"]:
                            lab=t["label"]; v=j.get(lab)
                            unit_=t.get("unit",""); dec=int(t.get("decimals",2))
                            if isinstance(v,(int,float)): v=round(float(v), dec)
                            set_value(lab,v,unit_,"lora"); snap[lab]=v
                        hist_push(snap)
                except serial.SerialException as e:
                    log_com_event("lora", "Perdida", f"Erro na porta serial: {e}")
                    log(f"LoRa: erro na porta serial: {e}")
                    break # Sai do loop interno para tentar reconectar
                except json.JSONDecodeError:
                    log(f"LoRa: JSON inválido recebido: {line}")
                except Exception as e:
                    log(f"LoRa: erro loop de leitura: {e}")
        except serial.SerialException as e:
            err = f"Falha ao abrir porta serial: {e}"
            log_com_event("lora", "Perdida", err)
            log(f"LoRa: {err}")
            for t in c["tags"]: set_error(t["label"], t.get("unit",""), "lora", err)
            time.sleep(5)
        finally:
            if ser and ser.is_open:
                ser.close()
                log("LoRa: Porta serial fechada.")

def worker_lorawan(my_id):
    # Esta implementação é baseada em MQTT
    c=CFG["lorawan"]; broker=c["broker"]; port=int(c["port"])
    username=c.get("username"); password=c.get("password")
    
    topics = {}
    for t in c.get("topics",[]):
        if t["topic"] not in topics:
            topics[t["topic"]] = []
        topics[t["topic"]].append((t["label"], t.get("unit",""), int(t.get("decimals",2)), t.get("json_key")))

    for t in c.get("topics",[]): set_value(t["label"], None, t.get("unit",""), "lorawan", "CONECTANDO")

    cli=mqtt.Client()
    if username: cli.username_pw_set(username, password)
    
    def on_connect(client,userdata,flags,rc):
        ok=(rc==0); log(f"LoRaWAN/MQTT connect rc={rc} (run_id={my_id})")
        msg = f"Conexão com broker {broker}:{port} OK" if ok else f"Falha na conexão com broker, rc={rc}"
        status = "Restabelecida" if ok else "Perdida"
        log_com_event("lorawan", status, msg)
        for topic, configs in topics.items():
            for (lab,unit_,_,_) in configs:
                if ok: client.subscribe(topic); set_value(lab,None,unit_,"lorawan","AGUARDANDO")
                else:  set_error(lab,unit_,"lorawan",f"rc={rc}")

    def on_message(client,userdata,msg):
        if not still_alive(my_id) or CFG.get("active_protocol")!="lorawan":
            return
        try:
            configs_for_topic = []
            for topic_template in topics.keys():
                if mqtt.topic_matches_sub(topic_template, msg.topic):
                    configs_for_topic = topics[topic_template]
                    break
            
            payload = json.loads(msg.payload.decode("utf-8"))
            
            for (lab,unit_,dec,jkey) in configs_for_topic:
                v = payload
                try: 
                    for k in jkey.split('.'): v = v[k]
                except (KeyError, TypeError):
                    log(f"LoRaWAN: Chave '{jkey}' não encontrada no payload do tópico {msg.topic}")
                    continue
                
                if isinstance(v,(int,float)): v=round(float(v), dec)
                set_value(lab,v,unit_,"lorawan")
        except Exception as e:
            log(f"LoRaWAN on_message erro: {e}")

    cli.on_connect=on_connect; cli.on_message=on_message

    back=1.0
    log(f"Worker LoRaWAN/MQTT iniciado (run_id={my_id})")
    while still_alive(my_id) and CFG.get("active_protocol")=="lorawan":
        try:
            cli.connect(broker,port,60); cli.loop_start()
            while still_alive(my_id) and CFG.get("active_protocol")=="lorawan":
                snap={"tempo": time.strftime("%H:%M:%S")}
                for t in c.get("topics", []):
                    lab=t["label"]; snap[lab]=STORE.get(lab, {}).get("value", None)
                hist_push(snap)
                time.sleep(max(0.5, CFG.get("refresh_ms",1000)/1000.0))
            cli.loop_stop(); back=1.0
        except Exception as e:
            err=f"LoRaWAN/MQTT Connect: {e}"
            log_com_event("lorawan", "Perdida", err)
            log(err); log(traceback.format_exc())
            for _, configs in topics.items():
                for (lab,unit_,_,_) in configs:
                    set_error(lab,unit_,"lorawan",err)
            time.sleep(back); back=min(back*2,10)
# ====================== IA / PDF / TXT ======================

def ia_headers():
    # Não precisa de lock para ler CFG, o risco é aceitável, pois CFG só é mudado por CONFIG_LOCK
    return {"Authorization": f"Bearer {CFG['ia'].get('api_key','')}",
            "Content-Type": "application/json"}

def ia_ask_question(question: str, analisar_logs: bool = False) -> str:
    snap = {"tempo": datetime.now().strftime("%H:%M:%S")}
    with STORE_LOCK: # Proteção de thread para leitura de STORE
        for k,v in STORE.items():
            snap[k] = v.get("value")

    log_context = ""
    if analisar_logs:
        with STORE_LOCK: # Proteção de thread para leitura de COM_LOG
            log_com = "\n".join(COM_LOG[-50:]) or "Nenhum evento de comunicação recente." # Pega os últimos 50 eventos
        log_dados = _generate_variation_log()
        log_context = f"\n\n--- INÍCIO LOGS ---\n\n[LOG DE COMUNICAÇÃO (ÚLTIMOS 50 EVENTOS)]\n{log_com}\n\n[LOG DE VARIAÇÃO DE DADOS (>10%)]\n{log_dados}\n\n--- FIM LOGS ---"

    prompt_content = f"PROTOCOLO_ATIVO: {CFG.get('active_protocol')}\nDADOS_ATUAIS(JSON): {json.dumps(snap, ensure_ascii=False)}{log_context}\n\nPERGUNTA DO USUÁRIO: {question}"
    
    payload = {
        "model": CFG["ia"].get("model",""),
        "messages": [
            {"role":"system","content":"Você é uma IA técnica para automação industrial. Responda de forma objetiva, analisando os dados atuais e, se fornecidos, os logs de comunicação e variação de dados para dar um diagnóstico ou insight técnico."},
            {"role":"user","content": prompt_content}
        ],
        "temperature": 0.2, "max_tokens": 1024
    }
    r = requests.post(CFG["ia"]["endpoint_chat"], headers=ia_headers(), json=payload, timeout=90)
    r.raise_for_status()
    j = r.json()
    return j.get("choices",[{}])[0].get("message",{}).get("content","").strip() or "Sem resposta."

def _try_set_unicode_font(pdf: FPDF) -> bool:
    candidates = ["/system/fonts/Roboto-Regular.ttf",
                  "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
                  "C:\\Windows\\Fonts\\arial.ttf"]
    for path in candidates:
        if os.path.exists(path):
            try:
                pdf.add_font("UX","",path,uni=True)
                pdf.set_font("UX", size=11)
                return True
            except Exception: pass
    return False

def generate_pdf(text: str) -> bytes:
    try:
        pdf=FPDF(); pdf.set_auto_page_break(auto=True, margin=15); pdf.add_page()
        pdf.set_font("Helvetica","B",14); pdf.cell(0,10,"Resposta da IA", ln=True)
        if not _try_set_unicode_font(pdf):
            pdf.set_font("Helvetica", size=11)
            text = (text or "").encode("latin-1","ignore").decode("latin-1")
        pdf.multi_cell(0,6, text or "Sem resposta.")
        pdf.ln(4); pdf.set_font(pdf.font_family if pdf.font_family=="UX" else "Helvetica", size=9)
        pdf.multi_cell(0,5, f"Gerado em {time.strftime('%d/%m/%Y %H:%M:%S')}")
        out = pdf.output(dest="S")
        return out.encode("latin-1","ignore") if isinstance(out,str) else out
    except Exception as e:
        pdf=FPDF(); pdf.add_page(); pdf.set_font("Helvetica", size=12)
        pdf.multi_cell(0,6, f"Falha ao gerar PDF: {e}")
        out = pdf.output(dest="S")
        return out.encode("latin-1","ignore") if isinstance(out,str) else out

# ====================== HTML UI ======================

HTML_INDEX = """
<!doctype html><html><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Cliente Universal – Gauges</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/raphael/2.3.0/raphael.min.js"></script>
<script src="https://cdnjs.cloudflare.com/ajax/libs/justgage/1.4.0/justgage.min.js"></script>
<style>
body{font-family:system-ui,Arial,sans-serif;margin:10px;background:#f5f7fb}
.wrap{max-width:1200px;margin:auto;}
.top{display:flex;gap:20px;flex-wrap:wrap;align-items:flex-start; margin-bottom: 10px;}
.protocol-select{display:flex;flex-direction:column;gap:5px;align-items:flex-start;}
select,button{padding:8px;border-radius:8px;border:1px solid #ccc; cursor: pointer; min-width: 120px;}
/* MELHORIA SOLICITADA: Layout da página principal (Gauges) com botões simétricos e em destaque. */
.btns{display:flex;gap:8px;flex-wrap:wrap;align-items:center; flex-grow: 1;}
.btns a { flex: 1 1 auto; }
.btns button { width: 100%; font-weight: bold; }
.grid{display:grid;grid-template-columns:repeat(auto-fill, minmax(160px, 1fr));gap:12px;margin-top:10px}
.card{background:#fff;border-radius:12px;box-shadow:0 1px 4px rgba(0,0,0,.1);padding:10px;width:160px;text-align:center}
.gauge-container{width:160px; height:130px;}
.gauge-label{font-weight:bold;font-size:1em;margin-top:0px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; 
             border: 1px dashed #ddd; 
             cursor: text; 
             min-height: 1.2em; 
             padding: 2px 4px;
             line-height: 1.2;}
</style>
</head><body>
<div class="wrap">
  <h2>Painel de Monitoramento</h2>
  <div class="top">
    <div class="protocol-select">
      <b>Protocolo Ativo:</b>
      <select id="proto" onchange="trocarProto()"></select>
    </div>
    <div class="btns">
      <a href="/grafico"><button>📈 Gráfico</button></a>
      <a href="/log.txt"><button>📝 Log Dados</button></a>
      <a href="/log/com.txt"><button>📡 Log COM</button></a>
      <a href="/ia"><button>🤖 IA</button></a>
      <a href="/setup"><button>⚙️ Configurar</button></a>
    </div>
  </div>
  <div id="grid" class="grid"></div>
</div>
<script>
let tags = [];
/* NEW: Função para salvar o nome personalizado no localStorage */
function saveGaugeLabel(el) {
    const key = el.getAttribute('data-key');
    const label = el.textContent.trim();
    localStorage.setItem('gauge_label_'+key, label);
}

function carregarUI(){
  fetch('/ui-config').then(r=>r.json()).then(j=>{
    document.getElementById('proto').innerHTML = ['modbus_tcp','modbus_rtu','mqtt','opcua','wifi','bluetooth','lora','lorawan']
      .map(p=>`<option value="${p}" ${p===j.active_protocol?'selected':''}>${p}</option>`).join('');
    tags = j.tags || [];
    const grid = document.getElementById('grid'); grid.innerHTML='';
    tags.forEach(t=>{
      const key = (t.label||t.topic||t.nodeid);
      const id = 'g_'+key.replace(/[^a-zA-Z0-9]/g, "_");
      const max = t.max || 100;
      const unit = t.unit||'';
      const color = t.color || '#4285F4';
      const decimals = (typeof t.decimals==='number')?t.decimals:0;
      /* NEW: Carrega o nome salvo (se existir) */
      const savedLabel = localStorage.getItem('gauge_label_'+key) || ''; 

      const div = document.createElement('div'); div.className='card';
      const gaugeDiv = document.createElement('div');
      gaugeDiv.id = id;
      gaugeDiv.className = 'gauge-container';
      
      /* ALTERADO: Label editável, usando savedLabel e salvando no 'onblur' */
      div.innerHTML = `<div class="gauge-label" title="${key}" contenteditable="true" data-key="${key}" onblur="saveGaugeLabel(this)">${savedLabel}</div>`;
      div.appendChild(gaugeDiv);
      grid.appendChild(div);

      const g = new JustGage({
        id: id,
        value: 0,
        min: 0,
        max: max,
        // REMOVIDO: symbol: unit, // Para retirar as unidades
        pointer: true,
        gaugeWidthScale: 0.6,
        levelColors: [color],
        startAnimationTime: 0,
        refreshAnimationTime: 0,
        humanFriendly: false,
        counter: true,
        decimals: decimals
      });

      div.gauge = g; div.key=key;
    });
  });
}
function atualizar(){
  fetch('/dados').then(r=>r.json()).then(d=>{
    const data = d.data || {};
    document.querySelectorAll('.card').forEach(div=>{
      if (!div.gauge) return;
      const k = div.key; let v = data[k]?.value ?? null;
      const valorReal = (typeof v === 'number') ? v : 0;
      div.gauge.refresh(valorReal);
    });
  });
}
function trocarProto(){
  const p = document.getElementById('proto').value;
  fetch('/api/config', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({active_protocol:p})})
    .then(_=>setTimeout(carregarUI,500));
}
carregarUI();
setInterval(atualizar, 1500);
</script>
</body></html>
"""

HTML_GRAFICO = """
<!DOCTYPE html>
<html><head>
<meta charset="utf-8"/><meta content="width=device-width, initial-scale=1" name="viewport"/>
<title>Gráfico + IA</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
<style>
body{font-family:system-ui,Arial;margin:10px;background:#f5f7fb}
.wrap{display:flex;gap:10px;flex-direction:column}
.card{background:#fff;padding:10px;border-radius:12px;box-shadow:0 1px 4px rgba(0,0,0,.1)}
input,button,textarea{padding:8px;border-radius:8px;border:1px solid #ccc;width:auto}
.btns{display:flex;gap:8px;flex-wrap:wrap;align-items:center}
#graf{width:100%;height:58vh}
small{color:#555}
</style>
</head><body>
<div class="wrap">
<div class="btns">
  <a href="/"><button>⬅️ Gauges</button></a>
  <button onclick="capturar()">📸 Capturar gráfico</button>
  <button id="freezeBtn" onclick="toggleFreeze()">Congelar Gráfico</button>
  <a href="/setup"><button>⚙️ Configurar</button></a>
</div>
<div class="card">
<canvas id="graf"></canvas>
<div class="btns" id="filtros"></div>
</div>
</div>
<script>
let graf=null, tagList=[], meta=[], labelMap={}; /* ALTERADO: labelMap adicionado */
let intervalId = null;
let isFrozen = false;

function loadTags(){ return fetch('/ui-config').then(r=>r.json()).then(j=>{
  const ts = (j.tags||j.topics||j.nodes||[]);
  tagList = ts.map(t=>(t.label||t.topic||t.nodeid));
  meta = ts.map(t=>({color:t.color||undefined, dec:(typeof t.decimals==='number')?t.decimals:2}));
  
  /* NEW: Carrega o mapeamento de labels salvos */
  labelMap = {};
  tagList.forEach(key => {
    const savedLabel = localStorage.getItem('gauge_label_'+key);
    labelMap[key] = savedLabel || key; /* Usa o nome salvo ou o original */
  });
});}

function initGraf(){
  const ctx=document.getElementById('graf').getContext('2d');
  graf=new Chart(ctx,{type:'line',data:{labels:[],datasets:[]},options:{
  responsive:true, maintainAspectRatio:false, animation:false,
  scales:{x:{title:{display:true,text:'Tempo'}}, y:{beginAtZero:true}},
  plugins:{legend:{display:true,onClick:(e,legendItem,legend)=>{
    const index = legendItem.datasetIndex;
    const ci = legend.chart;
    const vis = ci.data.datasets.map(d=>d.hidden);
    const onlyThis = vis.filter((v,i)=>i!==index).every(v=>v===true);
    ci.data.datasets.forEach((ds,i)=>{ds.hidden = onlyThis ? false : i !== index});
    ci.update();
  }}}
}});
}

function step(){
  fetch('/historico').then(r=>r.json()).then(h=>{
    if(!h.length) return; const latest=h.slice(-80);
    graf.data.labels=[...new Set(latest.map(x=>x.tempo))];
    const ds={};
    tagList.forEach((t,i)=>{ 
      const displayLabel = labelMap[t] || t; /* NEW: Pega o label a ser exibido (o nome personalizado) */
      ds[t]={label:displayLabel,data:[],borderWidth:2,fill:false}; /* ALTERADO: Usa o displayLabel no gráfico */
      if(meta[i].color) ds[t].borderColor = meta[i].color; 
    });
    latest.forEach(s=>{ tagList.forEach((t,i)=>{ let v = (t in s)?s[t]:null; if(typeof v==='number') v = Number(v.toFixed(meta[i].dec)); ds[t].data.push(v); }); });
    graf.data.datasets=Object.values(ds); graf.update();
  });
}

function capturar(){
  const canvas = document.getElementById('graf');
  const link = document.createElement('a');
  link.download = 'grafico.png';
  link.href = canvas.toDataURL('image/png');
  link.click();
}

function toggleFreeze() {
  const btn = document.getElementById('freezeBtn');
  isFrozen = !isFrozen;
  if (isFrozen) {
    clearInterval(intervalId);
    btn.textContent = 'Retomar Atualização';
  } else {
    intervalId = setInterval(step, 1500);
    btn.textContent = 'Congelar Gráfico';
  }
}

loadTags().then(() => {
  initGraf();
  step();
  intervalId = setInterval(step, 1500);
});
</script>
</body></html>
"""

HTML_SETUP = """
<!doctype html><html><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Configurações</title>
<style>
body{font-family:system-ui,Arial;margin:10px;background:#f5f7fb}
.card{background:#fff;padding:12px;border-radius:12px;box-shadow:0 1px 4px rgba(0,0,0,.1);margin-bottom:10px}
input,textarea,select,button{padding:8px;border-radius:8px;border:1px solid #ccc;width:100%;box-sizing:border-box;}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:10px}
small{color:#555} h3{margin:8px 0}
</style>
</head><body>
<a href="/"><button>⬅️ Voltar</button></a>

<div class="card">
  <h3>🎛️ Protocolo Ativo</h3>
  <select id="active">
    <option value="modbus_tcp">modbus_tcp</option>
    <option value="modbus_rtu">modbus_rtu</option>
    <option value="mqtt">mqtt</option>
    <option value="opcua">opcua</option>
    <option value="wifi">wifi</option>
    <option value="bluetooth">bluetooth</option>
    <option value="lora">lora</option>
    <option value="lorawan">lorawan</option>
  </select>
  <br><br>
  <label>Backend Modbus TCP</label>
  <select id="mt_backend">
    <option value="pyModbusTCP">pyModBusTCP</option>
    <option value="pymodbus">pymodbus</option>
  </select>
  <br><br>
  <label>Intervalo de Atualização (ms)</label>
  <input id="refresh_ms" type="number" placeholder="1000">
  <br><br>
  <button onclick="salvarActive()">💾 Salvar Protocolo/Backend</button>
</div>

<div class="card">
  <h3>🤖 IA</h3>
  <div class="grid">
    <div><label>Modelo</label><input id="ia_model" placeholder="llama-3.1-8b-instant"></div>
    <div><label>Chave API</label><input id="ia_key" placeholder="gsk_..." type="password"></div>
  </div>
  <small>Provedor: Groq. Salve o modelo e cole sua chave.</small><br><br>
  <button onclick="salvarIA()">💾 Salvar IA</button>
  <br><br>
  <button onclick="testarIA()">🔍 Testar chave</button>
  <p id="ia_msg"></p>
</div>

<div class="card">
  <h3>⚙️ Modbus TCP</h3>
  <div class="grid">
    <div><label>Host</label><input id="mt_host"></div>
    <div><label>Porta</label><input id="mt_port" type="number"></div>
    <div><label>Unit ID</label><input id="mt_uid" type="number"></div>
  </div>
  <br>
  <label>Tags (JSON)</label>
  <textarea id="mt_tags" rows="7"></textarea>
  <br><br>
  <button onclick="salvarModbusTCP()">💾 Salvar Modbus TCP</button>
</div>

<div class="card">
  <h3>⚙️ Modbus RTU</h3>
  <div class="grid">
    <div><label>Porta Serial</label><input id="mr_port" placeholder="/dev/ttyUSB0"></div>
    <div><label>Baudrate</label><input id="mr_baud" type="number" value="9600"></div>
    <div><label>Paridade (N/E/O)</label><input id="mr_parity" value="N"></div>
    <div><label>Stopbits</label><input id="mr_stop" type="number" value="1"></div>
    <div><label>Bytesize</label><input id="mr_bytes" type="number" value="8"></div>
    <div><label>Timeout (s)</label><input id="mr_to" type="number" value="1"></div>
    <div><label>Unit ID</label><input id="mr_uid" type="number" value="1"></div>
  </div>
  <br>
  <label>Tags (JSON)</label>
  <textarea id="mr_tags" rows="7"></textarea>
  <br><br>
  <button onclick="salvarModbusRTU()">💾 Salvar Modbus RTU</button>
</div>

<div class="card">
  <h3>⚙️ MQTT</h3>
  <div class="grid">
    <div><label>Broker</label><input id="mq_host" value="127.0.0.1"></div>
    <div><label>Porta</label><input id="mq_port" type="number" value="1883"></div>
  </div>
  <br>
  <label>Tópicos (JSON)</label>
  <textarea id="mq_topics" rows="7"></textarea>
  <br><br>
  <button onclick="salvarMQTT()">💾 Salvar MQTT</button>
</div>

<div class="card">
  <h3>⚙️ OPC UA</h3>
  <label>Endpoint</label>
  <input id="op_endpoint" value="opc.tcp://127.0.0.1:4840/freeopcua/server/">
  <br><br>
  <label>Nós (JSON)</label>
  <textarea id="op_nodes" rows="7"></textarea>
  <br><br>
  <button onclick="salvarOPCUA()">💾 Salvar OPC UA</button>
</div>

<div class="card">
  <h3>⚙️ Wi-Fi (socket)</h3>
  <div class="grid">
    <div><label>Host</label><input id="wifi_host" value="192.168.4.1"></div>
    <div><label>Porta</label><input id="wifi_port" type="number" value="12345"></div>
  </div>
  <br>
  <label>Tags (JSON)</label>
  <textarea id="wifi_tags" rows="5"></textarea>
  <small>O servidor remoto deve enviar JSON (ex: {"sensor1":12.3}) via socket TCP.</small>
  <br><br>
  <button onclick="salvarWIFI()">💾 Salvar Wi-Fi</button>
</div>

<div class="card">
  <h3>⚙️ Bluetooth (BLE)</h3>
  <div class="grid">
    <div><label>Endereço</label><input id="bt_addr" value="00:00:00:00:00:00"></div>
    <div><label>UUID (característica)</label><input id="bt_uuid" value="00001101-0000-1000-8000-00805F9B34FB"></div>
  </div>
  <br>
  <label>Tags (JSON)</label>
  <textarea id="bt_tags" rows="5"></textarea>
  <small>A característica deve retornar um payload JSON.</small>
  <br><br>
  <button onclick="salvarBT()">💾 Salvar Bluetooth</button>
</div>

<div class="card">
  <h3>⚙️ LoRa (Serial/P2P)</h3>
  <div class="grid">
    <div><label>Porta Serial</label><input id="lora_port" value="/dev/ttyUSB1"></div>
    <div><label>Baudrate</label><input id="lora_baud" type="number" value="115200"></div>
    <div><label>Timeout</label><input id="lora_timeout" type="number" value="2"></div>
  </div>
  <br>
  <label>Tags (JSON)</label>
  <textarea id="lora_tags" rows="5"></textarea>
  <small>O dispositivo LoRa deve enviar um JSON por linha na porta serial (ex: {"node1_temp":25.5}).</small>
  <br><br>
  <button onclick="salvarLORA()">💾 Salvar LoRa</button>
</div>

<div class="card">
  <h3>⚙️ LoRaWAN (via MQTT)</h3>
  <div class="grid">
    <div><label>Broker</label><input id="lorawan_broker" value="eu1.cloud.thethings.network"></div>
    <div><label>Porta</label><input id="lorawan_port" type="number" value="1883"></div>
    <div><label>Usuário</label><input id="lorawan_user"></div>
    <div><label>Senha</label><input id="lorawan_pass" type="password"></div>
  </div>
  <br>
  <label>Tópicos (JSON)</label>
  <textarea id="lorawan_topics" rows="7"></textarea>
  <small>Integração com LNS via MQTT. O campo 'json_key' extrai o valor do payload (ex: uplink_message.decoded_payload.temp).</small>
  <br><br>
  <button onclick="salvarLORAWAN()">💾 Salvar LoRaWAN</button>
</div>

<script>
function _v(id) { return document.getElementById(id).value; }
function _p(id) { return parseInt(_v(id)); }
function _f(id) { return parseFloat(_v(id)); }
function _j(id) { try { return JSON.parse(_v(id) || "[]"); } catch(e) { alert('Erro JSON no campo ' + id); throw e; } }
function loadAll(){
  fetch('/api/config').then(r=>r.json()).then(c=>{
    document.getElementById('active').value = c.active_protocol || 'modbus_tcp';
    document.getElementById('mt_backend').value = c.modbus_tcp?.backend || 'pyModbusTCP';
    document.getElementById('refresh_ms').value = c.refresh_ms || 1000;
    document.getElementById('ia_model').value = c.ia?.model || '';
    
    // Modbus TCP
    if(c.modbus_tcp){
        document.getElementById('mt_host').value = c.modbus_tcp.host || '127.0.0.1';
        document.getElementById('mt_port').value = c.modbus_tcp.port || 5020;
        document.getElementById('mt_uid').value  = c.modbus_tcp.unit_id || 1;
        document.getElementById('mt_tags').value = JSON.stringify(c.modbus_tcp.tags||[], null, 2);
    }
    // Modbus RTU
    if(c.modbus_rtu){
        document.getElementById('mr_port').value = c.modbus_rtu.port || '/dev/ttyUSB0';
        document.getElementById('mr_baud').value = c.modbus_rtu.baudrate || 9600;
        document.getElementById('mr_parity').value = c.modbus_rtu.parity || 'N';
        document.getElementById('mr_stop').value = c.modbus_rtu.stopbits || 1;
        document.getElementById('mr_bytes').value = c.modbus_rtu.bytesize || 8;
        document.getElementById('mr_to').value = c.modbus_rtu.timeout || 1;
        document.getElementById('mr_uid').value = c.modbus_rtu.unit_id || 1;
        document.getElementById('mr_tags').value = JSON.stringify(c.modbus_rtu.tags||[], null, 2);
    }
    // MQTT
    if(c.mqtt){
        document.getElementById('mq_host').value = c.mqtt.broker || '127.0.0.1';
        document.getElementById('mq_port').value = c.mqtt.port || 1883;
        document.getElementById('mq_topics').value = JSON.stringify(c.mqtt.topics||[], null, 2);
    }
    // OPC UA
    if(c.opcua){
        document.getElementById('op_endpoint').value = c.opcua.endpoint || 'opc.tcp://127.0.0.1:4840';
        document.getElementById('op_nodes').value = JSON.stringify(c.opcua.nodes||[], null, 2);
    }
    // WiFi
    if(c.wifi){
        document.getElementById('wifi_host').value = c.wifi.host || '192.168.4.1';
        document.getElementById('wifi_port').value = c.wifi.port || 12345;
        document.getElementById('wifi_tags').value = JSON.stringify(c.wifi.tags||[], null, 2);
    }
    // Bluetooth
    if(c.bluetooth){
        document.getElementById('bt_addr').value = c.bluetooth.address || '00:00:00:00:00:00';
        document.getElementById('bt_uuid').value = c.bluetooth.uuid || '';
        document.getElementById('bt_tags').value = JSON.stringify(c.bluetooth.tags||[], null, 2);
    }
    // LoRa
    if(c.lora){
        document.getElementById('lora_port').value = c.lora.port || '/dev/ttyUSB1';
        document.getElementById('lora_baud').value = c.lora.baudrate || 115200;
        document.getElementById('lora_timeout').value = c.lora.timeout || 2;
        document.getElementById('lora_tags').value = JSON.stringify(c.lora.tags||[], null, 2);
    }
    // LoRaWAN
    if(c.lorawan){
        document.getElementById('lorawan_broker').value = c.lorawan.broker || '';
        document.getElementById('lorawan_port').value = c.lorawan.port || 1883;
        document.getElementById('lorawan_user').value = c.lorawan.username || '';
        document.getElementById('lorawan_topics').value = JSON.stringify(c.lorawan.topics||[], null, 2);
    }
  });
}
function salvar(payload, msg){
    fetch('/api/config',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)})
    .then(_=>alert(msg));
}
function salvarActive(){
  salvar({
    active_protocol: _v('active'),
    refresh_ms: _p('refresh_ms') || 1000,
    modbus_tcp: { backend: _v('mt_backend') }
  }, '✅ Protocolo/Backend salvos e reiniciados.');
}
function salvarIA(){
  salvar({ia:{model:_v('ia_model').trim(), api_key:_v('ia_key').trim()}}, '✅ IA salva.');
}
function testarIA(){ fetch('/ia/testar').then(r=>r.json()).then(j=>alert(j.mensagem||JSON.stringify(j))); }
function salvarModbusTCP(){ salvar({modbus_tcp:{host:_v('mt_host'), port:_p('mt_port'), unit_id:_p('mt_uid'), tags:_j('mt_tags')}}, '✅ Modbus TCP salvo.'); }
function salvarModbusRTU(){ salvar({modbus_rtu:{port:_v('mr_port'), baudrate:_p('mr_baud'), parity:_v('mr_parity'), stopbits:_p('mr_stop'), bytesize:_p('mr_bytes'), timeout:_f('mr_to'), unit_id:_p('mr_uid'), tags:_j('mr_tags')}}, '✅ Modbus RTU salvo.'); }
function salvarMQTT(){ salvar({mqtt:{broker:_v('mq_host'), port:_p('mq_port'), topics:_j('mq_topics')}}, '✅ MQTT salvo.'); }
function salvarOPCUA(){ salvar({opcua:{endpoint:_v('op_endpoint'), nodes:_j('op_nodes')}}, '✅ OPC UA salvo.'); }
function salvarWIFI(){ salvar({wifi:{host:_v('wifi_host'), port:_p('wifi_port'), tags:_j('wifi_tags')}}, '✅ Wi-Fi salvo.'); }
function salvarBT(){ salvar({bluetooth:{address:_v('bt_addr'), uuid:_v('bt_uuid'), tags:_j('bt_tags')}}, '✅ Bluetooth salvo.'); }
function salvarLORA(){ salvar({lora:{port:_v('lora_port'), baudrate:_p('lora_baud'), timeout:_p('lora_timeout'), tags:_j('lora_tags')}}, '✅ LoRa salvo.'); }
function salvarLORAWAN(){ salvar({lorawan:{broker:_v('lorawan_broker'), port:_p('lorawan_port'), username:_v('lorawan_user'), password:_v('lorawan_pass'), topics:_j('lorawan_topics')}}, '✅ LoRaWAN salvo.'); }
loadAll();
</script>
</body></html>
"""


# ====================== ROTAS: DADOS, LOGS, IA ======================

@APP.route("/ui-config")
def ui_config():
    # A leitura de CFG no web thread é de baixo risco, mas pode ser protegida se necessário.
    # Por convenção, só protegemos modificações do CFG e acessos críticos em workers.
    ap = CFG.get("active_protocol")
    keys = {"modbus_tcp": "tags", "modbus_rtu": "tags", "mqtt": "topics", "opcua": "nodes", "wifi": "tags", "bluetooth": "tags", "lora": "tags", "lorawan": "topics"}
    tags = CFG.get(ap, {}).get(keys.get(ap), [])
    return jsonify({"active_protocol": ap, "tags": tags})

@APP.route("/dados")
def dados():
    with STORE_LOCK: cur=dict(STORE)
    return jsonify({"updated": time.strftime("%H:%M:%S"), "data": cur})

@APP.route("/historico")
def historico(): 
    with STORE_LOCK:
        return jsonify(HIST)

@APP.route("/ia/perguntar", methods=["POST"])
def ia_ask():
    global ULTIMA_RESPOSTA # Necessário para modificar o global
    
    j = request.get_json(silent=True) or {}
    p = (j.get("pergunta") or "").strip()
    analisar_logs = j.get("analisar_logs", False)
    if not p: return jsonify({"resposta":"⛔ Pergunta vazia."})
    
    try:
        temp_resposta = ia_ask_question(p, analisar_logs)
        
        with STORE_LOCK: # Proteção de thread para modificação
            ULTIMA_RESPOSTA = temp_resposta
            
    except requests.Timeout:
        with STORE_LOCK:
            ULTIMA_RESPOSTA = "⛔ Timeout (90s) ao consultar a IA."
    except Exception as e:
        with STORE_LOCK:
            ULTIMA_RESPOSTA = f"⛔ Erro ao consultar a IA: {e}"
            
    return jsonify({"resposta": ULTIMA_RESPOSTA})

@APP.route("/ia/clear")
def ia_clear():
    global ULTIMA_RESPOSTA # Necessário para modificar o global
    with STORE_LOCK:
        ULTIMA_RESPOSTA = ""
    return jsonify({"ok": True})

@APP.route("/ia/pdf")
def ia_pdf():
    with STORE_LOCK:
        texto = ULTIMA_RESPOSTA or "Sem resposta disponível."
    return send_file(io.BytesIO(generate_pdf(texto)), mimetype="application/pdf", as_attachment=True, download_name="resposta_IA.pdf")

@APP.route("/ia/txt")
def ia_txt():
    with STORE_LOCK:
        txt = (ULTIMA_RESPOSTA or "Sem resposta disponível.").encode("utf-8")
    return send_file(io.BytesIO(txt), as_attachment=True, download_name="resposta_IA.txt", mimetype="text/plain")

def _generate_variation_log() -> str:
    # Acessa HIST, já protegido indiretamente por hist_push (e acessos via /historico)
    # Mas como é lido dentro de ia_ask_question, que usa STORE_LOCK para COM_LOG,
    # A leitura de HIST no for loop não precisa de lock adicional pois já está implícita
    # ou é uma cópia segura. Para garantir, vamos usar STORE_LOCK aqui também.
    with STORE_LOCK:
        if len(HIST) < 2: return "Histórico insuficiente para análise."
        log_lines = []
        for i in range(1, len(HIST)):
            s_atual, s_anterior = HIST[i], HIST[i-1]
            for key, val_atual in s_atual.items():
                if key == 'tempo': continue
                val_anterior = s_anterior.get(key)
                if isinstance(val_atual, (int, float)) and isinstance(val_anterior, (int, float)):
                    if val_anterior != 0 and abs(val_atual - val_anterior) / abs(val_anterior) > 0.10:
                        status = "Alta" if val_atual > val_anterior else "Queda"
                        log_lines.append(f"{s_atual.get('tempo', '')} | ALERTA: {status} >10% em '{key}' | Anterior: {val_anterior}, Atual: {val_atual}")
        return "\n".join(log_lines) or "Nenhuma variação significativa (>10%) detectada."

@APP.route("/log.txt")
def download_txt_log():
    # _generate_variation_log já usa STORE_LOCK
    log_content = _generate_variation_log()
    return send_file(io.BytesIO(log_content.encode("utf-8")), as_attachment=True, download_name="log_dados.txt", mimetype="text/plain")

@APP.route("/log/com.txt")
def download_com_log():
    with STORE_LOCK: # Proteção de thread para leitura de COM_LOG
        log_content = "\n".join(COM_LOG)
    return send_file(io.BytesIO(log_content.encode("utf-8")), as_attachment=True, download_name="log_comunicacao.txt", mimetype="text/plain")

@APP.route("/ia/testar")
def ia_testar():
    try:
        r = requests.get(CFG["ia"]["endpoint_models"], headers=ia_headers(), timeout=15)
        r.raise_for_status()
        return jsonify({"ok":True,"mensagem":"✅ Chave válida."})
    except Exception as e:
        return jsonify({"ok":False,"mensagem":f"⛔ Erro: {e}"})

# ====================== ROTAS: CONFIG / SETUP ======================

@APP.route("/api/config", methods=["GET","POST"])
def api_config():
    global CFG
    if request.method=="POST":
        data=request.get_json(force=True, silent=True) or {}
        def merge(dst, src):
            for k,v in src.items():
                if isinstance(v, dict) and isinstance(dst.get(k), dict): merge(dst[k], v)
                else: dst[k]=v
        
        with CONFIG_LOCK: # Proteção de thread para modificação e salvamento de CFG
            merge(CFG, data); save_cfg(CFG)
            
        with STORE_LOCK: STORE.clear() # Limpa o STORE de dados, não precisa de CONFIG_LOCK
        start_workers()
        return jsonify({"ok":True})
    
    # GET Request:
    safe=json.loads(json.dumps(CFG))
    if "ia" in safe and "api_key" in safe["ia"]: safe["ia"]["api_key"]="***"
    if "lorawan" in safe and "password" in safe["lorawan"]: safe["lorawan"]["password"]="***"
    return jsonify(safe)

@APP.route("/")
def page_index(): return render_template_string(HTML_INDEX)

@APP.route("/grafico")
def page_graph(): return render_template_string(HTML_GRAFICO)

@APP.route("/setup")
def page_setup(): return render_template_string(HTML_SETUP)


# ====================== MAIN / PÁGINA IA ======================

HTML_IA = """
<!doctype html><html><head>
<meta charset='utf-8'><meta name='viewport' content='width=device-width, initial-scale=1'>
<title>Painel IA</title>
<style>
body{font-family:sans-serif;margin:10px;background:#f5f7fb}
.wrap{max-width:800px;margin:auto;display:flex;flex-direction:column;gap:12px}
.card{background:#fff;padding:12px;border-radius:12px;box-shadow:0 1px 4px rgba(0,0,0,.1)}
textarea,button{padding:8px;border-radius:8px;border:1px solid #ccc;width:100%;box-sizing:border-box}
.btns{display:flex;gap:8px;flex-wrap:wrap} button{width:auto;}
</style>
</head><body>
<div class='wrap'>
  <div class='btns'>
    <a href='/'><button>⬅️ Gauges</button></a>
    <a href='/grafico'><button>📈 Gráfico</button></a>
    <a href='/setup'><button>⚙️ Configurar</button></a>
  </div>
  <div class='card'>
    <label>Faça uma pergunta para a IA</label>
    <textarea id='pergunta' rows='3' placeholder='Ex: O que pode ter causado a queda de tensão? Analise os logs para encontrar correlações.'></textarea>
    <br><br>
    <div class='btns'>
      <button onclick='perguntar(false)'>🤖 Perguntar (Dados Atuais)</button>
      <button onclick='perguntar(true)'>📝 Analisar Logs com IA</button>
    </div>
    <br>
    <label>Resposta</label>
    <textarea id='resposta' style="height:45vh" readonly></textarea>
  </div>
  <div class='btns'>
    <button onclick='baixarTXT()'>📝 Baixar TXT</button>
    <button onclick='baixarPDF()'>📄 Baixar PDF</button>
    <button onclick='limparIA()'>🧹 Limpar</button>
  </div>
</div>
<script>
function perguntar(analisarLogs){
  const p=document.getElementById('pergunta').value.trim();
  if(!p){alert('Digite a pergunta');return;}
  document.getElementById('resposta').value='⏳ Consultando IA... (Analisando logs: '+analisarLogs+')';
  const payload = {pergunta: p, analisar_logs: analisarLogs};
  fetch('/ia/perguntar',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)})
    .then(r=>r.json()).then(j=>{document.getElementById('resposta').value=j.resposta||'';})
    .catch(e=>{document.getElementById('resposta').value='⛔ Erro de rede: ' + e;});
}
function limparIA(){
  document.getElementById('pergunta').value='';
  document.getElementById('resposta').value='';
  fetch('/ia/clear');
}
function baixarPDF(){window.location='/ia/pdf?t='+Date.now();}
function baixarTXT(){window.location='/ia/txt?t='+Date.now();}
</script>
</body></html>
"""

@APP.route("/ia")
def page_ia():
    return render_template_string(HTML_IA)
# ====================== IA HÍBRIDA (LOCAL + NUVEM) ======================

# Limites técnicos (ajuste conforme planta)
IA_LIMITES = {
    "corrente_max": 1.20,     # 120% nominal
    "temperatura_max": 80.0,  # °C
    "variacao_pct": 0.10      # 10%
}

IA_HIBRIDA_ATIVA = True
IA_INTERVALO = 10  # segundos entre análises locais
_IA_ULTIMA_ANALISE = 0


def ia_local_analisador():
    """
    IA LOCAL:
    Analisa STORE, HIST e COM_LOG.
    Retorna lista de eventos relevantes.
    """
    eventos = []

    with STORE_LOCK:
        corrente = STORE.get("corrente", {}).get("value")
        temperatura = STORE.get("temperatura", {}).get("value")

    # Regras diretas
    if isinstance(corrente, (int, float)):
        if corrente > IA_LIMITES["corrente_max"] * (corrente / IA_LIMITES["corrente_max"]):
            eventos.append("sobrecorrente")

    if isinstance(temperatura, (int, float)):
        if temperatura > IA_LIMITES["temperatura_max"]:
            eventos.append("sobretemperatura")

    # Análise de variação (>10%)
    with STORE_LOCK:
        if len(HIST) >= 2:
            atual = HIST[-1]
            anterior = HIST[-2]
            for k in atual:
                if k == "tempo":
                    continue
                va = atual.get(k)
                vb = anterior.get(k)
                if isinstance(va, (int, float)) and isinstance(vb, (int, float)) and vb != 0:
                    if abs(va - vb) / abs(vb) > IA_LIMITES["variacao_pct"]:
                        eventos.append(f"variacao_brusca_{k}")

    # Falhas de comunicação recentes
    with STORE_LOCK:
        falhas = [l for l in COM_LOG[-10:] if "PERDIDA" in l.upper()]
        if len(falhas) >= 3:
            eventos.append("instabilidade_comunicacao")

    return list(set(eventos))


def ia_hibrida_loop():
    """
    Loop contínuo da IA híbrida.
    Roda em background.
    """
    global _IA_ULTIMA_ANALISE, ULTIMA_RESPOSTA

    while True:
        try:
            if not IA_HIBRIDA_ATIVA:
                time.sleep(IA_INTERVALO)
                continue

            agora = time.time()
            if agora - _IA_ULTIMA_ANALISE < IA_INTERVALO:
                time.sleep(1)
                continue

            _IA_ULTIMA_ANALISE = agora

            eventos = ia_local_analisador()

            # Critério de escalonamento para IA NUVEM
            if len(eventos) >= 2:
                pergunta = (
                    "Detectada anomalia operacional.\n"
                    f"Eventos: {', '.join(eventos)}.\n"
                    "Analise os dados atuais, histórico e logs "
                    "e forneça diagnóstico técnico e ação recomendada."
                )

                log("IA HÍBRIDA: escalonando para IA nuvem")
                resposta = ia_ask_question(pergunta, analisar_logs=True)

                with STORE_LOCK:
                    ULTIMA_RESPOSTA = resposta

        except Exception as e:
            log(f"IA HÍBRIDA ERRO: {e}")

        time.sleep(IA_INTERVALO)


# ====================== START IA HÍBRIDA ======================

def start_ia_hibrida():
    t = threading.Thread(target=ia_hibrida_loop, daemon=True)
    t.start()
    log("IA HÍBRIDA iniciada (LOCAL + NUVEM)")


# Garante que a IA híbrida sobe junto com o sistema
start_ia_hibrida()

if __name__=="__main__":
    start_workers()
    APP.run(host="0.0.0.0", port=5001, debug=False)
