"""
DMS Data Collector — session_storage.py
Architecture simplifiée (retour aux fondamentaux) :
  sessions.db  → métadonnées de sessions + données génériques sujet
  dataset/     → fichiers HDF5 + CSV

Le subject_id est généré automatiquement (subject_001, subject_002...).
Les métadonnées collectées sont strictement génériques :
  tranche_age, genre, port_lunettes, conditions_med (optionnel)
"""

import os, sqlite3, json
from datetime import datetime, timezone

# ── Constantes ─────────────────────────────────────────────────
DATA_ROOT   = "dataset"
SESSIONS_DB = "sessions.db"

CLASSES = {
    "distraction":   ["telephone_G", "telephone_D", "sms",
                      "boisson", "radio", "passager"],
    "fatigue":       ["baillement", "microsommeil", "conduite_somnolente"],
    "attentiveness": ["conduite_normale"],
    "gaze":          ["regard_retroviseur", "regard_fenetre_G", "regard_fenetre_D"],
}

TRANCHES_AGE  = ["18–25", "26–35", "36–45", "46–55", "56+"]
GENRES        = ["Femme", "Homme"]
LUNETTES      = ["Non", "Oui — lunettes", "Oui — lentilles"]

# ── Connexion SQLite ────────────────────────────────────────────
def _db():
    conn = sqlite3.connect(SESSIONS_DB)
    conn.row_factory = sqlite3.Row
    return conn


def init_sessions_db():
    """Crée la table sessions si elle n'existe pas."""
    with _db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                session_id       TEXT PRIMARY KEY,
                subject_id       TEXT NOT NULL,
                tranche_age      TEXT DEFAULT '',
                genre            TEXT DEFAULT '',
                port_lunettes    TEXT DEFAULT '',
                conditions_med   TEXT DEFAULT '',
                label_class      TEXT NOT NULL,
                label_activity   TEXT NOT NULL,
                duration_s       INTEGER,
                duration_real_s  REAL,
                eeg_rows         INTEGER DEFAULT 0,
                bracelet_rows    INTEGER DEFAULT 0,
                driving_rows     INTEGER DEFAULT 0,
                camera_rows      INTEGER DEFAULT 0,
                depth_rows       INTEGER DEFAULT 0,
                devices          TEXT,
                sync_method      TEXT DEFAULT 'SyncClock_perf_counter',
                validation_ok    INTEGER DEFAULT 0,
                session_path     TEXT,
                created_at       TEXT
            )
        """)
        conn.commit()


# ── Gestion des sujets ─────────────────────────────────────────

def generate_next_subject_id() -> str:
    """
    Génère le prochain subject_id disponible.
    Compte les subject_id distincts dans sessions.db → subject_00N+1.
    Retourne 'subject_001' si la base est vide.
    """
    init_sessions_db()
    with _db() as conn:
        row = conn.execute(
            "SELECT COUNT(DISTINCT subject_id) as n FROM sessions"
        ).fetchone()
    return f"subject_{(row['n'] + 1):03d}"


def get_all_subject_ids() -> list:
    """Retourne la liste des subject_id distincts déjà enregistrés."""
    init_sessions_db()
    with _db() as conn:
        rows = conn.execute(
            "SELECT DISTINCT subject_id FROM sessions ORDER BY subject_id"
        ).fetchall()
    return [r["subject_id"] for r in rows]


def get_subject_session_count(subject_id: str) -> int:
    """Nombre de sessions enregistrées pour un subject_id."""
    init_sessions_db()
    with _db() as conn:
        row = conn.execute(
            "SELECT COUNT(*) as n FROM sessions WHERE subject_id=?",
            (subject_id,)
        ).fetchone()
    return row["n"]


# ── Gestion des sessions ───────────────────────────────────────

def create_session_path(label_class: str, subject_id: str):
    """Crée le dossier de session et retourne (path, session_id)."""
    timestamp  = datetime.now().strftime("%Y%m%d_%H%M%S")
    session_id = f"session_{timestamp}"
    path = os.path.join(DATA_ROOT, label_class, subject_id, session_id)
    os.makedirs(path, exist_ok=True)
    return path, session_id


def insert_session(session_id, subject_id, tranche_age, genre,
                   port_lunettes, conditions_med,
                   label_class, label_activity,
                   duration_s, duration_real, devices, session_path,
                   eeg_rows=0, bracelet_rows=0,
                   driving_rows=0, camera_rows=0, depth_rows=0,
                   validation_ok=False):
    """Insère ou met à jour une session dans sessions.db."""
    init_sessions_db()
    with _db() as conn:
        conn.execute("""
            INSERT OR REPLACE INTO sessions (
                session_id, subject_id,
                tranche_age, genre, port_lunettes, conditions_med,
                label_class, label_activity,
                duration_s, duration_real_s,
                eeg_rows, bracelet_rows, driving_rows, camera_rows, depth_rows,
                devices, validation_ok, session_path, created_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            session_id, subject_id,
            tranche_age, genre, port_lunettes, conditions_med,
            label_class, label_activity,
            duration_s, round(duration_real, 2),
            eeg_rows, bracelet_rows, driving_rows, camera_rows, depth_rows,
            json.dumps(devices),
            1 if validation_ok else 0,
            session_path,
            datetime.now(timezone.utc).isoformat()
        ))
        conn.commit()


def get_all_sessions() -> list:
    """Toutes les sessions, ordre décroissant."""
    init_sessions_db()
    with _db() as conn:
        rows = conn.execute(
            "SELECT * FROM sessions ORDER BY created_at DESC"
        ).fetchall()
    return [dict(r) for r in rows]


def get_sessions_stats() -> dict:
    """Nombre de sessions par classe."""
    init_sessions_db()
    with _db() as conn:
        rows = conn.execute(
            "SELECT label_class, COUNT(*) as n FROM sessions GROUP BY label_class"
        ).fetchall()
    stats = {cls: 0 for cls in CLASSES}
    for row in rows:
        if row["label_class"] in stats:
            stats[row["label_class"]] = row["n"]
    return stats


# ── Validation HDF5 ────────────────────────────────────────────

def validate_hdf5(session_path: str) -> dict:
    """Vérifie les fichiers HDF5 et CSV après la session.
    depth_timestamps.csv est optionnel (présent si OpenNI2 activé).
    """
    import h5py
    checks = {
        "EEG":      {"file": "eeg.h5",               "min": 5,  "hdf5": True,  "required": True},
        "Bracelet": {"file": "bracelet.h5",           "min": 5,  "hdf5": True,  "required": True},
        "Volant":   {"file": "driving.h5",            "min": 50, "hdf5": True,  "required": True},
        "Caméra":   {"file": "timestamps_video.csv",  "min": 20, "hdf5": False, "required": True},
        "Depth":    {"file": "depth_timestamps.csv",  "min": 0,  "hdf5": False, "required": False},
    }
    results    = {}
    all_ok     = True
    row_counts = {"eeg": 0, "bracelet": 0, "driving": 0, "camera": 0, "depth": 0}
    keys       = {"EEG": "eeg", "Bracelet": "bracelet", "Volant": "driving",
                  "Caméra": "camera", "Depth": "depth"}

    for dev, info in checks.items():
        fpath = os.path.join(session_path, info["file"])
        if not os.path.exists(fpath):
            if info["required"]:
                results[dev] = {"rows": 0, "ok": False, "msg": "Fichier manquant"}
                all_ok = False
            else:
                results[dev] = {"rows": 0, "ok": True, "msg": "Non disponible (optionnel)"}
            continue
        if info["hdf5"]:
            try:
                with h5py.File(fpath, "r") as f:
                    rows = int(f["elapsed_s"].shape[0])  # type: ignore[union-attr]
            except Exception:
                rows = 0
        else:
            with open(fpath, encoding="utf-8") as f:
                rows = sum(1 for _ in f) - 1
        ok = rows >= info["min"]
        if not ok and info["required"]:
            all_ok = False
        results[dev] = {"rows": rows, "ok": ok, "msg": f"{rows} lignes"}
        row_counts[keys[dev]] = rows

    results["_global_ok"]  = all_ok
    results["_row_counts"] = row_counts
    return results


def save_session_report(session_path: str, validation: dict, duration_real: float):
    """Génère session_report.txt."""
    lines = [
        "=== Rapport de session DMS v8 ===",
        f"Chemin    : {session_path}",
        f"Durée     : {duration_real:.1f} s",
        f"Stockage  : HDF5 (signaux) + SQLite (métadonnées) + CSV (timestamps vidéo)",
        "",
        "--- Validation des fichiers ---",
    ]
    for dev, info in validation.items():
        if dev.startswith("_"):
            continue
        status = "OK" if info["ok"] else "PROBLEME"
        lines.append(f"  {dev:10s} : {status} — {info['msg']}")
    lines.append("")
    lines.append(
        "Statut global : " +
        ("OK — session valide" if validation["_global_ok"]
         else "ATTENTION — vérifier les fichiers")
    )
    with open(os.path.join(session_path, "session_report.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines))