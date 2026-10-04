"""
WheelReader — v2  (Logitech G29 — mode connecté via pygame.joystick)
=====================================================================
Colonnes HDF5 : elapsed_s, steering, steering_deg, throttle, brake, clutch, label
Fréquence     : 30 Hz

MODE CONNECTÉ — Logitech G29 :
  Axes pygame (confirmés G29 Windows) :
    axis 0 → volant    [-1.0 ; +1.0]   (gauche → droite)
    axis 1 → accélérateur [+1.0 ; -1.0] (inversé : repos=+1, fond=-1)
    axis 2 → frein        [+1.0 ; -1.0] (idem)
    axis 3 → embrayage    [+1.0 ; -1.0] (idem)

  Les pédales sont normalisées en [0.0 ; 1.0] :
    val_normalisée = (1.0 - axe_brut) / 2.0

PRÉREQUIS :
  pip install pygame
  Brancher le G29 en USB avant de lancer l'app.
"""

import threading
import time
import os
import random

import h5py

from .sync_clock import SyncClock

# ══ CONFIGURATION ══════════════════════════════════════════════════════════════
TARGET_HZ     = 30        # fréquence d'acquisition
FLUSH_EVERY   = 30        # flush HDF5 toutes les 30 lignes (~1 s)
JOYSTICK_IDX  = 0         # index pygame du G29 (0 si c'est le seul joystick)

# Indices des axes pygame pour le G29
AXIS_STEERING     = 0
AXIS_THROTTLE     = 1     # inversé : repos = +1.0
AXIS_BRAKE        = 2     # inversé : repos = +1.0
AXIS_CLUTCH       = 3     # inversé : repos = +1.0
# ═══════════════════════════════════════════════════════════════════════════════

COLS = ["elapsed_s", "steering", "steering_deg", "throttle", "brake", "clutch"]


def _normalize_pedal(raw: float) -> float:
    """Convertit un axe pédale inversé [-1,+1] en [0.0,+1.0]."""
    return round((1.0 - raw) / 2.0, 4)


class WheelReader:

    def __init__(self, session_path: str, label: str, simulated: bool = True):
        self.file_path = os.path.join(session_path, "driving.h5")
        self.label     = label
        self.simulated = simulated
        self.running   = False
        self._thread   = None
        self.last      = {"steering": 0.0, "throttle": 0.0, "brake": 0.0}
        self._buf      = {c: [] for c in COLS}
        self._lbuf     = []

    def start(self):
        self.running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self.running = False
        if self._thread:
            self._thread.join(timeout=5)

    def _run(self):
        if self.simulated:
            self._run_simulated()
        else:
            self._run_connected()

    # ── MODE SIMULATION ────────────────────────────────────────────────────────
    def _run_simulated(self):
        with h5py.File(self.file_path, "w") as f:
            ds, ds_label = self._create_datasets(f)
            count = 0
            while self.running:
                st  = round(random.uniform(-1.0, 1.0), 3)
                thr = round(random.uniform(0.0, 1.0), 3)
                brk = round(random.uniform(0.0, 0.3), 3)
                clt = 0.0
                self.last = {"steering": st, "throttle": thr, "brake": brk}
                row = [SyncClock.elapsed(), st, round(st * 450, 1), thr, brk, clt]
                count = self._buffer_row(row, count, ds, ds_label, f)
                time.sleep(1.0 / TARGET_HZ)
            self._flush_final(ds, ds_label, f)

    # ── MODE CONNECTÉ ──────────────────────────────────────────────────────────
    def _run_connected(self):
        """Lecture du Logitech G29 via pygame.joystick à 30 Hz."""
        try:
            import pygame
        except ImportError:
            print("[Volant] ERREUR : pygame non installé.")
            print("[Volant] → Lancer : pip install pygame")
            print("[Volant] → Basculement en simulation")
            self._run_simulated()
            return

        # Initialiser pygame en mode headless (sans fenêtre)
        pygame.init()
        pygame.joystick.init()

        n_joysticks = pygame.joystick.get_count()
        if n_joysticks == 0:
            print("[Volant] ERREUR : aucun joystick/volant détecté.")
            print("[Volant] → Vérifier le branchement USB du G29")
            print("[Volant] → Basculement en simulation")
            pygame.quit()
            self._run_simulated()
            return

        try:
            joy = pygame.joystick.Joystick(JOYSTICK_IDX)
            joy.init()
            print(f"[Volant] ✅ {joy.get_name()} détecté "
                  f"({joy.get_numaxes()} axes, {joy.get_numbuttons()} boutons)")
        except pygame.error as e:
            print(f"[Volant] ERREUR ouverture joystick : {e}")
            pygame.quit()
            self._run_simulated()
            return

        interval  = 1.0 / TARGET_HZ
        next_tick = time.perf_counter()

        with h5py.File(self.file_path, "w") as f:
            ds, ds_label = self._create_datasets(f)
            count = 0

            try:
                while self.running:
                    # Pomper les événements pygame (obligatoire pour mise à jour des axes)
                    pygame.event.pump()

                    # Lire les axes
                    st_raw  = joy.get_axis(AXIS_STEERING)  if joy.get_numaxes() > AXIS_STEERING  else 0.0
                    thr_raw = joy.get_axis(AXIS_THROTTLE) if joy.get_numaxes() > AXIS_THROTTLE else -1.0
                    brk_raw = joy.get_axis(AXIS_BRAKE)    if joy.get_numaxes() > AXIS_BRAKE    else -1.0
                    clt_raw = joy.get_axis(AXIS_CLUTCH)   if joy.get_numaxes() > AXIS_CLUTCH   else -1.0

                    # Normaliser
                    st  = round(st_raw, 4)
                    thr = _normalize_pedal(thr_raw)
                    brk = _normalize_pedal(brk_raw)
                    clt = _normalize_pedal(clt_raw)

                    self.last = {"steering": st, "throttle": thr, "brake": brk}

                    row = [SyncClock.elapsed(), st, round(st * 450, 1), thr, brk, clt]
                    count = self._buffer_row(row, count, ds, ds_label, f)

                    # Cadence 30 Hz
                    next_tick += interval
                    sleep_time = next_tick - time.perf_counter()
                    if sleep_time > 0:
                        time.sleep(sleep_time)

            finally:
                self._flush_final(ds, ds_label, f)
                joy.quit()
                pygame.quit()
                print(f"[Volant] ✅ Déconnecté — {ds['elapsed_s'].shape[0]} lignes écrites")

    # ── HELPERS HDF5 ───────────────────────────────────────────────────────────
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

    def _buffer_row(self, row: list, count: int, ds, ds_label, f) -> int:
        """Ajoute une ligne au buffer et flush si seuil atteint. Retourne le nouveau count."""
        for i, col in enumerate(COLS):
            self._buf[col].append(row[i])
        self._lbuf.append(self.label)
        count += 1

        if count >= FLUSH_EVERY:
            self._flush(ds, ds_label, f)
            return 0
        return count

    def _flush(self, ds, ds_label, f):
        """Flush le buffer courant dans le HDF5."""
        n = len(self._lbuf)
        if n == 0:
            return
        for col in COLS:
            old = ds[col].shape[0]
            ds[col].resize((old + n,))
            ds[col][old:] = self._buf[col]
            self._buf[col] = []
        old = ds_label.shape[0]
        ds_label.resize((old + n,))
        ds_label[old:] = self._lbuf
        self._lbuf = []
        f.flush()

    def _flush_final(self, ds, ds_label, f):
        """Flush des données restantes au stop."""
        if self._lbuf:
            self._flush(ds, ds_label, f)