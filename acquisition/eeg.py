"""
acquisition/eeg.py  —  NeuroSky MindWave 2 — Driver RFCOMM direct
=====================================================================

"""

import threading
import time
import random
import os
import struct
import socket
import json 

import h5py

from .sync_clock import SyncClock

BT_ADDRESS    = "C4:64:E3:E3:05:2F"
BT_CHANNEL    = 1
CONNECT_RETRY = 10
CONNECT_DELAY = 8.0
RECV_TIMEOUT  = 5.0

COLS = ["elapsed_s", "attention", "meditation",
        "signal_quality", "blink", "delta", "theta",
        "alpha_low", "alpha_high", "beta_low", "beta_high",
        "gamma_low", "gamma_mid"]


class ThinkGearParser:

    def __init__(self):
        self._buf = bytearray()

    def feed(self, data: bytes) -> list[dict]:
        self._buf.extend(data)
        packets = []
        while True:
            pkt = self._try_parse()
            if pkt is None:
                break
            packets.append(pkt)
        return packets

    def _try_parse(self) -> dict | None:
        buf = self._buf
        while len(buf) >= 2:
            if buf[0] == 0xAA and buf[1] == 0xAA:
                break
            buf.pop(0)
        if len(buf) < 4:
            return None
        plen = buf[2]
        if plen == 0xAA:
            buf.pop(0)
            return None
        total = 3 + plen + 1
        if len(buf) < total:
            return None
        payload  = buf[3:3 + plen]
        checksum = buf[3 + plen]
        if (~sum(payload) & 0xFF) != checksum:
            buf.pop(0)
            return None
        del buf[:total]
        return self._decode_payload(bytes(payload))

    def _decode_payload(self, payload: bytes) -> dict:
        pkt = {
            "attention": None, "meditation": None,
            "signal_quality": None, "blink": None, "raw": None,
            "delta": 0, "theta": 0,
            "alpha_low": 0, "alpha_high": 0,
            "beta_low": 0, "beta_high": 0,
            "gamma_low": 0, "gamma_mid": 0,
        }
        i = 0
        while i < len(payload):
            code = payload[i]; i += 1
            if code == 0x02:
                if i < len(payload):
                    pkt["signal_quality"] = payload[i]; i += 1
            elif code == 0x04:
                if i < len(payload):
                    pkt["attention"] = payload[i]; i += 1
            elif code == 0x05:
                if i < len(payload):
                    pkt["meditation"] = payload[i]; i += 1
            elif code == 0x16:
                if i < len(payload):
                    pkt["blink"] = payload[i]; i += 1
            elif code >= 0x80:
                if i >= len(payload):
                    break
                vlen = payload[i]; i += 1
                # Protection trame incomplète
                if i + vlen > len(payload):
                    break
                vdata = payload[i:i + vlen]; i += vlen
                try:
                    if code == 0x80 and vlen == 2:
                        pkt["raw"] = struct.unpack(">h", vdata)[0]
                    elif code == 0x83 and vlen == 24:
                        vals = struct.unpack(">IIIIIIII", vdata)
                        for k, v in zip(
                            ["delta", "theta", "alpha_low", "alpha_high",
                             "beta_low", "beta_high", "gamma_low", "gamma_mid"],
                            vals
                        ):
                            pkt[k] = v
                except struct.error:
                    # Ignorer silencieusement les trames malformées
                    pass
            else:
                i += 1
        return pkt


class EEGReader:

    def __init__(self, session_path: str, label: str, simulated: bool = True):
        self.file_path = os.path.join(session_path, "eeg.h5")
        self.label     = label
        self.simulated = simulated
        self.running   = False
        self._thread   = None
        self._lock     = threading.Lock()
        self._stop_evt = threading.Event()
        self.last = {
            "attention": 0, "meditation": 0,
            "signal_quality": 200, "blink": 0,
            "delta": 0, "theta": 0,
            "alpha_low": 0, "alpha_high": 0,
            "beta_low": 0, "beta_high": 0,
            "gamma_low": 0, "gamma_mid": 0,
        }

    def start(self):
        self.running = True
        self._stop_evt.clear()
        self._thread = threading.Thread(
            target=self._run, daemon=True, name="eeg-reader")
        self._thread.start()

    def stop(self):
        self.running = False
        self._stop_evt.set()
        if self._thread:
            self._thread.join(timeout=8)
            self._thread = None

    def _run(self):
        if self.simulated:
            self._run_simulated()
        else:
            self._run_connected()

    def _run_simulated(self):
        with h5py.File(self.file_path, "w") as f:
            ds, ds_label = self._create_datasets(f)
            while not self._stop_evt.is_set():
                att = random.randint(30, 90)
                med = random.randint(30, 80)
                with self._lock:
                    self.last["attention"]      = att
                    self.last["meditation"]     = med
                    self.last["signal_quality"] = 0
                self._write_row(ds, ds_label, f,
                    [SyncClock.elapsed(), att, med, 0, 0,
                     0, 0, 0, 0, 0, 0, 0, 0])
                time.sleep(1.0)

    def _run_connected(self):
        retry = 0
        while self.running:
            try:
                self._rfcomm_session()
                retry = 0
            except Exception as e:
                print(f"[EEG] Erreur session : {e}")
            if self.running:
                wait = min(5 + retry * 2, 30)
                print(f"[EEG] Reconnexion dans {wait}s... "
                      f"(éteindre/rallumer le casque si nécessaire)")
                self._stop_evt.wait(wait)
                retry += 1

    def _rfcomm_session(self):
        sock = None
        for attempt in range(1, CONNECT_RETRY + 1):
            try:
                print(f"[EEG] Connexion RFCOMM {BT_ADDRESS} "
                      f"canal {BT_CHANNEL} "
                      f"(tentative {attempt}/{CONNECT_RETRY})...")
                sock = socket.socket(
                    socket.AF_BLUETOOTH,
                    socket.SOCK_STREAM,
                    socket.BTPROTO_RFCOMM
                )
                sock.settimeout(15.0)
                sock.connect((BT_ADDRESS, BT_CHANNEL))
                sock.settimeout(RECV_TIMEOUT)
                print("[EEG] ✅ Connecté via RFCOMM")
                break
            except Exception as e:
                print(f"[EEG] Tentative {attempt} échouée : {e}")
                if sock:
                    try: sock.close()
                    except Exception: pass
                    sock = None
                if attempt == 3:
                    print("[EEG] ⚠️  Éteins et rallume le casque !")
                if self.running:
                    self._stop_evt.wait(CONNECT_DELAY)
        else:
            raise RuntimeError(
                f"Impossible de se connecter après {CONNECT_RETRY} tentatives.\n"
                f"→ Éteins le casque, attends 10s, rallume-le."
            )

        # ═════════════════════════════════════════════════════
        # ║  COMMANDE D'INITIALISATION   ║
        # ═════════════════════════════════════════════════════
        try:
            # Cette commande JSON est comprise par le MindWave Mobile 2
            # et démarre le flux de données ThinkGear.
            init_cmd = json.dumps({
                "enableRawOutput": False,   # Pas de raw EEG (trop de data pour 512Hz)
                "format": "Json"           # On ne s'en sert pas en direct, 
                                            # mais cela active le flux binaire.
            }) + "\r\n"
            print("[EEG] Envoi commande d'initialisation...")
            sock.sendall(init_cmd.encode('utf-8'))
            print("[EEG] Commande envoyée, attente de l'initialisation...")
            time.sleep(1.5)  # Essentiel : laisser le casque traiter la commande
            print("[EEG] Initialisation terminée, écoute du flux...")
        except Exception as e:
            raise RuntimeError(f"Échec de l'initialisation du flux EEG : {e}")
        # ═════════════════════════════════════════════════════

        parser    = ThinkGearParser()
        empty_cnt = 0

        with h5py.File(self.file_path, "w") as f:
            ds, ds_label = self._create_datasets(f)
            try:
                print("[EEG] 🧠 Collecte démarrée — mets le casque sur la tête")
                while self.running:
                    try:
                        raw = sock.recv(256)
                    except socket.timeout:
                        continue
                    except OSError as e:
                        raise RuntimeError(f"Socket erreur : {e}")

                    if not raw:
                        empty_cnt += 1
                        print(f"[EEG] ⚠️  Flux vide ({empty_cnt}) — "
                              f"casque bien sur la tête ?")
                        if empty_cnt >= 5:
                            raise RuntimeError("Flux vide — casque déconnecté")
                        time.sleep(3)
                        continue

                    empty_cnt = 0
                    for pkt in parser.feed(raw):
                        self._process_packet(pkt, ds, ds_label, f)

            finally:
                try: sock.close()
                except Exception: pass
                print("[EEG] 🔌 Déconnecté")

    def _process_packet(self, pkt: dict, ds, ds_label, f):
        sig = pkt.get("signal_quality")
        att = pkt.get("attention")
        med = pkt.get("meditation")

        if sig is not None:
            q = "OK" if sig == 0 else f"faible ({sig}/200)"
            print(f"[EEG] Signal {q}")
        if att is not None:
            print(f"[EEG] Attention={att}  Méditation={med}")

        with self._lock:
            if sig is not None:
                self.last["signal_quality"] = sig
            if att is not None:
                self.last["attention"]  = att
                self.last["meditation"] = med or 0
            if pkt.get("blink") is not None:
                self.last["blink"] = pkt["blink"]
            for band in ("delta", "theta", "alpha_low", "alpha_high",
                         "beta_low", "beta_high", "gamma_low", "gamma_mid"):
                if pkt.get(band):
                    self.last[band] = pkt[band]

        if att is not None and med is not None:
            self._write_row(ds, ds_label, f, [
                SyncClock.elapsed(), att, med,
                sig if sig is not None else 200,
                pkt.get("blink") or 0,
                pkt.get("delta", 0), pkt.get("theta", 0),
                pkt.get("alpha_low", 0), pkt.get("alpha_high", 0),
                pkt.get("beta_low", 0), pkt.get("beta_high", 0),
                pkt.get("gamma_low", 0), pkt.get("gamma_mid", 0),
            ])

    def _create_datasets(self, f):
        ds = {}
        for col in COLS:
            ds[col] = f.create_dataset(
                col, shape=(0,), maxshape=(None,),
                dtype="float32", compression="gzip", compression_opts=4
            )
        ds_label = f.create_dataset(
            "label", shape=(0,), maxshape=(None,),
            dtype=h5py.special_dtype(vlen=str)
        )
        return ds, ds_label

    def _write_row(self, ds, ds_label, f, row):
        for i, col in enumerate(COLS):
            ds[col].resize((ds[col].shape[0] + 1,))
            ds[col][-1] = row[i]
        ds_label.resize((ds_label.shape[0] + 1,))
        ds_label[-1] = self.label
        f.flush()