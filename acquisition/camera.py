"""
CameraReader (RGB via OpenCV + DEPTH via OpenNI2)
========================================================
 
PRÉREQUIS pour le mode depth :
  pip install openni
  SDK OpenNI2 installé : https://www.orbbec.com/developers/openni-sdk/
"""

import threading, time, os, csv
import numpy as np
from datetime import datetime, timezone
from .sync_clock import SyncClock

# ══ CONFIGURATION ══════════════════════════════════════════════════════════════
CAMERA_INDEX     = 1      # index OpenCV pour le flux RGB
TARGET_FPS       = 20     # FPS cible pour le flux RGB
TARGET_DEPTH_FPS = 29     # FPS cible pour le flux DEPTH
SAVE_VIDEO       = True   # Enregistrer video_rgb.mp4
SAVE_DEPTH       = True   # Enregistrer depth_timestamps.csv
VIDEO_WIDTH      = 640
VIDEO_HEIGHT     = 480
DEPTH_WIDTH      = 640   
DEPTH_HEIGHT     = 480    
 
# Chemin vers les DLLs OpenNI2 si non dans PATH système :
OPENNI2_libs_PATH = r"C:\OpenNI2\sdk\libs"
# ═══════════════════════════════════════════════════════════════


def _init_openni2():
    """
    Tente d'initialiser OpenNI2. Retourne le module openni2 ou None si
    indisponible (le CameraReader bascule alors en RGB seul).
    """
    try:
        from openni import openni2 as _oni2
    except ImportError:
        print("[Camera] openni non installé → depth désactivé")
        print("[Camera] Pour activer : pip install openni")
        return None
 
    try:
        _oni2.initialize()
        return _oni2
    except Exception:
        pass
 
    if os.path.isdir(OPENNI2_libs_PATH):
        try:
            _oni2.initialize(OPENNI2_libs_PATH)
            return _oni2
        except Exception as e:
            print(f"[Camera] OpenNI2 init échoué : {e} → depth désactivé")
    else:
        print(f"[Camera] SDK OpenNI2 introuvable ({OPENNI2_libs_PATH})")
        print("[Camera] Télécharger : https://www.orbbec.com/developers/openni-sdk/")
 
    return None

class CameraReader:
    def __init__(self, session_path: str, label: str, simulated: bool = True):
        self.session_path = session_path
        self.label        = label
        self.simulated    = simulated
        self.running      = False
        self._thread      = None
        # Compteurs exposés au Dashboard
        self.last = {
            "frames":         0,   # frames RGB
            "depth_frames":   0,   # frames depth
            "depth_mm":       0.0, # dernière profondeur moyenne (mm)
        }

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

    # ── MODE SIMULATION ───────────────────────────────────────
    def _run_simulated(self):
        """Génère des timestamps CSV sans matériel réel."""
        ts_path    = os.path.join(self.session_path, "timestamps_video.csv")
        depth_path = os.path.join(self.session_path, "depth_timestamps.csv")
        frame_idx  = 0
 
        with open(ts_path,    "w", newline="", encoding="utf-8") as f_rgb, \
             open(depth_path, "w", newline="", encoding="utf-8") as f_dep:
 
            w_rgb = csv.writer(f_rgb)
            w_dep = csv.writer(f_dep)
            w_rgb.writerow(["elapsed_s", "frame", "timestamp_utc", "label"])
            w_dep.writerow(["elapsed_s", "frame", "depth_mean_mm",
                            "depth_min_mm", "label"])
 
            while self.running:
                elapsed = SyncClock.elapsed()
                ts_utc  = datetime.now(timezone.utc).isoformat()
 
                # RGB simulé
                w_rgb.writerow([elapsed, frame_idx, ts_utc, self.label])
                f_rgb.flush()
 
                # Depth simulé (valeurs réalistes autour de 700–1500 mm)
                sim_mean = round(1000 + 200 * np.sin(elapsed * 0.5), 1)
                sim_min  = round(sim_mean - 150, 1)
                w_dep.writerow([elapsed, frame_idx, sim_mean,
                                sim_min, self.label])
                f_dep.flush()
 
                frame_idx                    += 1
                self.last["frames"]           = frame_idx
                self.last["depth_frames"]     = frame_idx
                self.last["depth_mm"]         = sim_mean
 
                time.sleep(1.0 / TARGET_FPS)

    # ── MODE CONNECTÉ ─────────────────────────────────────────
    def _run_connected(self):
        """
        Lance en parallèle :
          - Thread principal : OpenCV → video_rgb.mp4 + timestamps_video.csv
          - Thread depth     : OpenNI2 → depth_timestamps.csv
        """
        try:
            import cv2
        except ImportError:
            print("[Camera] ERREUR : opencv-python non installé.")
            self._run_simulated()
            return
 
        ts_path    = os.path.join(self.session_path, "timestamps_video.csv")
        video_path = os.path.join(self.session_path, "video_rgb.mp4")
        depth_path = os.path.join(self.session_path, "depth_timestamps.csv")

        # ── Ouvrir la caméra RGB (OpenCV) ─────────────────────────────────────
        cap = cv2.VideoCapture(CAMERA_INDEX, cv2.CAP_MSMF)
        if not cap.isOpened():
            cap = cv2.VideoCapture(CAMERA_INDEX)
        if not cap.isOpened():
            print(f"[Camera] ERREUR : impossible d'ouvrir index={CAMERA_INDEX}")
            self._run_simulated()
            return
 
        cap.set(cv2.CAP_PROP_FRAME_WIDTH,  VIDEO_WIDTH)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, VIDEO_HEIGHT)
        cap.set(cv2.CAP_PROP_FPS,          TARGET_FPS)
 
        actual_w   = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        actual_h   = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        actual_fps = cap.get(cv2.CAP_PROP_FPS) or TARGET_FPS
        print(f"[Camera] RGB ouverte — {actual_w}×{actual_h} @ {actual_fps:.0f} fps")
 
        writer = None
        if SAVE_VIDEO:
            writer = self._open_writer(cv2, video_path, actual_w, actual_h)
 
        # ── Tenter d'ouvrir OpenNI2 pour le flux Depth ────────────────────────
        oni2         = _init_openni2()
        depth_thread = None
 
        if oni2 and SAVE_DEPTH:
            depth_thread = threading.Thread(
                target=self._run_depth_stream,
                args=(oni2, depth_path),
                daemon=True
            )
            depth_thread.start()
            print("[Camera] ✅ Thread depth OpenNI2 démarré")
        else:
            print("[Camera] ⚠️  Flux depth désactivé — RGB seul")
            # Créer un CSV vide pour ne pas casser la validation post-session
            with open(depth_path, "w", newline="", encoding="utf-8") as f:
                csv.writer(f).writerow(["elapsed_s", "frame",
                                        "depth_mean_mm", "depth_min_mm", "label"])

        # ── Boucle RGB principale ──────────────────────────────────────────────
        frame_idx = 0
        interval  = 1.0 / TARGET_FPS
        next_tick = time.perf_counter()
 
        with open(ts_path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["elapsed_s", "frame", "timestamp_utc", "label"])
 
            while self.running:
                ret, frame = cap.read()
                if not ret:
                    print("[Camera] Erreur lecture frame RGB — retry...")
                    time.sleep(0.05)
                    continue
 
                elapsed = SyncClock.elapsed()
                ts_utc  = datetime.now(timezone.utc).isoformat()
 
                w.writerow([elapsed, frame_idx, ts_utc, self.label])
                f.flush()
 
                if writer and writer.isOpened():
                    writer.write(frame)
 
                frame_idx           += 1
                self.last["frames"]  = frame_idx
 
                next_tick  += interval
                sleep_time  = next_tick - time.perf_counter()
                if sleep_time > 0:
                    time.sleep(sleep_time)
 
        # ── Nettoyage RGB ──────────────────────────────────────────────────────
        cap.release()
        if writer:
            writer.release()
            final_path = getattr(self, "_actual_video_path", video_path)
            if os.path.exists(final_path) and os.path.getsize(final_path) > 0:
                size_kb = os.path.getsize(final_path) // 1024
                print(f"[Camera] ✅ {os.path.basename(final_path)} "
                      f"— {frame_idx} frames — {size_kb} Ko")
            else:
                print(f"[Camera] ⚠️  Fichier vidéo vide : {final_path}")
 
        # ── Attendre la fin du thread depth ────────────────────────────────────
        if depth_thread and depth_thread.is_alive():
            depth_thread.join(timeout=3)
 
        if oni2:
            try:
                oni2.unload()
            except Exception:
                pass
 
    # ── THREAD DEPTH (OpenNI2) ─────────────────────────────────────────────────
    def _run_depth_stream(self, oni2, depth_path: str):
        """
        Thread indépendant : capture le flux depth via OpenNI2 et écrit
        depth_timestamps.csv avec les colonnes :
          elapsed_s | frame | depth_mean_mm | depth_min_mm | label
 
        L'elapsed_s est le même que celui du thread RGB (SyncClock partagé),
        ce qui permet l'alignement via pandas.merge_asof().
        """
        try:
            device = oni2.Device.open_any()
        except Exception as e:
            print(f"[Depth] ERREUR ouverture caméra : {e}")
            return
 
        # Activer l'alignement depth → RGB si supporté par le modèle
        try:
            device.set_image_registration_mode(
                oni2.IMAGE_REGISTRATION_DEPTH_TO_COLOR
            )
            print("[Depth] ✅ Alignement depth→RGB activé")
        except Exception:
            print("[Depth] ⚠️  Alignement depth→RGB non disponible (ignoré)")
 
        depth_stream = device.create_depth_stream()
        depth_stream.start()
        print(f"[Depth] ✅ Flux démarré — {DEPTH_WIDTH}×{DEPTH_HEIGHT} "
              f"@ {TARGET_DEPTH_FPS} fps cible")
 
        frame_idx = 0
        interval  = 1.0 / TARGET_DEPTH_FPS
        next_tick = time.perf_counter()
 
        with open(depth_path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["elapsed_s", "frame", "depth_mean_mm",
                        "depth_min_mm", "label"])
 
            while self.running:
                depth_frame = depth_stream.read_frame()
                if depth_frame is None:
                    continue
 
                # Extraire la carte de profondeur (uint16, valeurs en mm)
                data  = np.frombuffer(
                    depth_frame.get_buffer_as_uint16(), dtype=np.uint16
                ).reshape(depth_frame.height, depth_frame.width)
 
                valid = data[data > 0]  # ignorer pixels hors plage (=0)
                if len(valid) > 0:
                    depth_mean = round(float(valid.mean()), 1)
                    depth_min  = int(valid.min())
                else:
                    depth_mean = 0.0
                    depth_min  = 0
 
                elapsed = SyncClock.elapsed()   # horloge partagée
                w.writerow([elapsed, frame_idx, depth_mean,
                            depth_min, self.label])
                f.flush()
 
                frame_idx                    += 1
                self.last["depth_frames"]     = frame_idx
                self.last["depth_mm"]         = depth_mean
 
                next_tick  += interval
                sleep_time  = next_tick - time.perf_counter()
                if sleep_time > 0:
                    time.sleep(sleep_time)
 
        depth_stream.stop()
        print(f"[Depth] ✅ Terminé — {frame_idx} frames depth enregistrées")
 
    # ── CODEC VIDEO ────────────────────────────────────────────────────────────
    def _open_writer(self, cv2, path, w, h):
        """Essaie plusieurs codecs dans l'ordre jusqu'à en trouver un valide."""
        codecs_to_try = [
            ("avc1", ".mp4"),
            ("H264", ".mp4"),
            ("mp4v", ".mp4"),
            ("MJPG", ".avi"),
            ("XVID", ".avi"),
        ]
        for codec, ext in codecs_to_try:
            out_path = path if ext == ".mp4" else path.replace(".mp4", ".avi")
            try:
                fourcc = cv2.VideoWriter_fourcc(*codec)
                writer = cv2.VideoWriter(out_path, fourcc, TARGET_FPS, (w, h))
                if writer.isOpened():
                    test_frame = np.zeros((h, w, 3), dtype=np.uint8)
                    writer.write(test_frame)
                    self._actual_video_path = out_path
                    print(f"[Camera] ✅ Codec {codec} → "
                          f"{os.path.basename(out_path)}")
                    return writer
                writer.release()
            except Exception as e:
                print(f"[Camera] Codec {codec} échoué : {e}")
 
        print("[Camera] ⚠️  Aucun codec disponible — CSV uniquement")
        return None