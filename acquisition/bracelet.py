"""
acquisition/bracelet.py  —  H59MAX iSo Tech — Driver BLE
=====================================================================
Protocole confirmé par diagnostic :
  - Commande : AB0004FF65000066 (START_CONTINU) sur RX1 (6e400002)
  - Données  : notify sur TX1 (6e400003)
  - Trame    : 16 bytes, header 0x73 0x12
                byte[6]  = HR brut (pas toujours fiable)
                byte[10] = HR stabilisé (valeur principale)
                byte[12] = SpO2

"""

import asyncio
import threading
import time
import random
import os

import h5py
from bleak import BleakClient, BleakScanner

from .sync_clock import SyncClock

# ── Config ────────────────────────────────────────────────────────────────────
ADDRESS        = "30:37:44:36:56:03"
RX_UUID        = "6e400002-b5a3-f393-e0a9-e50e24dcca9e"
TX_UUID        = "6e400003-b5a3-f393-e0a9-e50e24dcca9e"
CMD_START      = bytes.fromhex("AB0004FF65000066")   # START_CONTINU confirmé
CMD_STOP       = bytes.fromhex("AB0004FF62010068")   # STOP
GATT_DELAY     = 4.0    # délai stabilisation WinRT
KEEPALIVE_INTV = 5.0    # renvoi CMD_START toutes les 5s
COLS           = ["elapsed_s", "heart_rate", "spo2", "hrv", "stress"]


# ── Loop BLE global (singleton) ───────────────────────────────────────────────
# UN SEUL loop asyncio pour toute l'app.
# Résout le crash "Event loop is closed" / conflits WinRT multi-threads.
_ble_loop:   asyncio.AbstractEventLoop | None = None
_ble_thread: threading.Thread | None = None
_ble_lock    = threading.Lock()


def _get_ble_loop() -> asyncio.AbstractEventLoop:
    global _ble_loop, _ble_thread
    with _ble_lock:
        if _ble_loop and _ble_loop.is_running():
            return _ble_loop
        loop = asyncio.new_event_loop()
        def _worker():
            asyncio.set_event_loop(loop)
            loop.run_forever()
        t = threading.Thread(target=_worker, daemon=True, name="ble-worker")
        t.start()
        # Attendre que le loop soit vraiment démarré
        deadline = time.time() + 2.0
        while not loop.is_running() and time.time() < deadline:
            time.sleep(0.02)
        _ble_loop   = loop
        _ble_thread = t
        return loop


# ── Décodeur trame H59MAX ─────────────────────────────────────────────────────
# Trame confirmée (16 bytes) :
#   [0]  = 0x73  header
#   [1]  = 0x12  type mesure temps réel
#   [2-3]= 0x00  réservé
#   [4]  = compteur séquence (uint8)
#   [5]  = 0x00
#   [6]  = HR brut (souvent instable au démarrage)
#   [7-9]= données supplémentaires
#   [10] = HR stabilisé (valeur principale fiable)
#   [11] = 0x00
#   [12] = SpO2 (%)
#   [13-14] = 0x00
#   [15] = checksum XOR

def decode_frame(data: bytes) -> dict | None:
    if len(data) < 16:
        return None

    # Trame temps réel confirmée (header 0x73 0x12)
    if data[0] == 0x73 and data[1] == 0x12:
        # byte[10] = HR en bpm (direct, confirmé expérimentalement)
        # Le capteur monte progressivement : ignorer valeurs < 40 (chauffe)
        hr = data[10]
        if hr < 40 or hr > 220:
            hr = 0

        # SpO2 byte[12] — disponible après ~60s de contact cutané
        spo2 = data[12]
        if not (50 <= spo2 <= 100):
            spo2 = 0

        # Retourner même si hr=0 (pour compter les trames reçues)
        return {"heart_rate": hr, "spo2": spo2, "hrv": 0, "stress": 0}

    # Trame ACK/status — ignorer silencieusement
    if data[0] == 0x73 and data[1] == 0x01:
        return None

    return None


# ── BraceletReader ────────────────────────────────────────────────────────────

class BraceletReader:

    def __init__(self, session_path: str, label: str, simulated: bool = True):
        self.file_path = os.path.join(session_path, "bracelet.h5")
        self.label     = label
        self.simulated = simulated
        self.running   = False
        self._lock     = threading.Lock()
        self._hdf      = None
        self._ds       = None
        self._ds_label = None
        self._stop_evt = threading.Event()
        self._future   = None          # Future de la session BLE
        self._sim_t    = None          # Thread simulation
        self.last = {"heart_rate": 0, "spo2": 0, "hrv": 0, "stress": 0}

    # ── Public ───────────────────────────────────────────────────────────────

    def start(self):
        self.running = True
        self._stop_evt.clear()
        if self.simulated:
            self._sim_t = threading.Thread(
                target=self._run_simulated, daemon=True, name="brac-sim")
            self._sim_t.start()
        else:
            loop = _get_ble_loop()
            self._future = asyncio.run_coroutine_threadsafe(
                self._ble_manager(), loop)

    def stop(self):
        self.running = False
        self._stop_evt.set()
        if self._future:
            try:
                self._future.result(timeout=12)
            except Exception:
                pass
            self._future = None
        if self._sim_t:
            self._sim_t.join(timeout=5)
            self._sim_t = None

    # ── Simulation ───────────────────────────────────────────────────────────

    def _run_simulated(self):
        with h5py.File(self.file_path, "w") as f:
            ds, ds_label = self._create_datasets(f)
            while not self._stop_evt.is_set():
                hr     = random.randint(60, 90)
                spo2   = random.randint(95, 99)
                hrv    = random.randint(30, 80)
                stress = random.randint(10, 50)
                with self._lock:
                    self.last = {"heart_rate": hr, "spo2": spo2,
                                 "hrv": hrv, "stress": stress}
                self._write_row(ds, ds_label, f,
                    [SyncClock.elapsed(), hr, spo2, hrv, stress])
                time.sleep(1.0)

    # ── BLE manager ──────────────────────────────────────────────────────────

    async def _ble_manager(self):
        """Gère les reconnexions automatiques."""
        retry = 0
        with h5py.File(self.file_path, "w") as f:
            self._hdf      = f
            self._ds, self._ds_label = self._create_datasets(f)
            try:
                while self.running:
                    try:
                        await self._ble_session()
                        retry = 0
                    except Exception as e:
                        print(f"[Bracelet] Session terminée : {e}")
                    if self.running:
                        wait = min(5 + retry * 2, 30)
                        print(f"[Bracelet] Reconnexion dans {wait}s...")
                        await asyncio.sleep(wait)
                        retry += 1
            finally:
                self._hdf = None

    # ── Session BLE ───────────────────────────────────────────────────────────

    async def _ble_session(self):
        # Vérifier que le device est visible avant de tenter la connexion
        print(f"[Bracelet] Scan {ADDRESS}...")
        dev = await BleakScanner.find_device_by_address(ADDRESS, timeout=8.0)
        if dev is None:
            raise RuntimeError("Device introuvable au scan BLE")
        print(f"[Bracelet] Trouvé : {dev.name}")

        print(f"[Bracelet] Connexion...")
        client = BleakClient(ADDRESS, timeout=20.0)
        await client.connect()
        if not client.is_connected:
            raise ConnectionError("Échec connexion")
        print("[Bracelet] ✅ Connecté")

        try:
            # Délai obligatoire Windows WinRT
            print(f"[Bracelet] Stabilisation GATT {GATT_DELAY}s...")
            await asyncio.sleep(GATT_DELAY)
            if not client.is_connected:
                raise ConnectionError("Déconnecté pendant stabilisation")

            # Activer notify sur TX1
            await client.start_notify(TX_UUID, self._on_notify)
            print("[Bracelet] ✅ Notify TX1 activé")

            # Pas de CMD_START nécessaire — le bracelet H59MAX envoie
            # ses données spontanément dès que les notifications sont activées
            print("[Bracelet] 📡 Collecte démarrée — données spontanées...")

            # Boucle de maintien simple
            while self.running and client.is_connected:
                await asyncio.sleep(1.0)

        finally:
            if client.is_connected:
                try:
                    await client.stop_notify(TX_UUID)
                except Exception:
                    pass
                try:
                    await client.write_gatt_char(
                        RX_UUID, CMD_STOP, response=False)
                except Exception:
                    pass
                try:
                    await client.disconnect()
                except Exception:
                    pass
            print("[Bracelet] 🔌 Déconnecté")

    # ── Callback notify ───────────────────────────────────────────────────────

    def _on_notify(self, sender, data: bytes):
        print(f"[Bracelet] Raw {data.hex()}")
        dec = decode_frame(data)
        if dec is None:
            return
        print(f"[Bracelet] ❤️  HR={dec['heart_rate']} bpm  SpO2={dec['spo2']}%")
        with self._lock:
            self.last = {k: dec[k] for k in ("heart_rate", "spo2", "hrv", "stress")}
        if self._hdf is not None:
            self._write_row(
                self._ds, self._ds_label, self._hdf,
                [SyncClock.elapsed(),
                 dec["heart_rate"], dec["spo2"], dec["hrv"], dec["stress"]]
            )

    # ── HDF5 ─────────────────────────────────────────────────────────────────

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