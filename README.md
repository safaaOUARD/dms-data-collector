# DMS Data Collector

**Prototype multisensoriel de surveillance du conducteur**  
Projet de Fin d'Année — ENSAJ · Filière ISIC · 2025/2026  
Réalisé par : Rajae Elkamili, Safaa Ouard, Houda Riad  
Encadrante : Prof. Asmaa El Hannani

---

## Matériel requis

| Capteur | Modèle | Interface |
|---|---|---|
| EEG | NeuroSky MindWave Mobile 2 | Bluetooth RFCOMM |
| Bracelet | H59MAX iSo Tech | BLE (Bleak) |
| Caméra 3D | ORBBEC Astra Pro | USB (OpenCV + OpenNI2) |
| Volant | Logitech G29 Driving Force | USB HID |

---

## Installation

```bash
pip install -r requirements.txt
```

Pour activer le flux depth (caméra 3D) :
1. Télécharger le SDK OpenNI2 : https://www.orbbec.com/developers/openni-sdk/
2. Décommenter `openni>=2.3.0` dans `requirements.txt`
3. `pip install openni`

---

## Lancement

```bash
python app.py
```

Login admin par défaut : **dms2026**

---

## Architecture

```
DMS_Final/
├── app.py                      # Interface PyQt6 — écran principal
├── acquisition/
│   ├── sync_clock.py           # Horloge partagée (perf_counter, <1ms)
│   ├── eeg.py                  # NeuroSky MindWave — RFCOMM direct
│   ├── bracelet.py             # H59MAX iSo Tech — BLE
│   ├── camera.py               # ORBBEC Astra Pro — RGB + Depth
│   └── wheel.py                # Logitech G29 — 30 Hz
├── storage/
│   └── session_storage.py      # SQLite + HDF5 + CSV
├── dataset/                    # Données collectées (généré au runtime)
└── sessions.db                 # Base de métadonnées (générée au runtime)
```

## Stockage des données

- **SQLite** (`sessions.db`) — métadonnées de sessions, identifiants anonymes
- **HDF5 compressé** — signaux EEG (13 colonnes), bracelet (5 col.), volant (7 col.)
- **CSV** — timestamps vidéo RGB + depth (profondeur mm)
- **MP4/AVI** — vidéo RGB de conduite

## Classes comportementales

| Classe | Activités |
|---|---|
| `distraction` | téléphone G/D, SMS, boisson, radio, passager |
| `fatigue` | bâillement, micro-sommeil, conduite somnolente |
| `attentiveness` | conduite normale |
| `gaze` | regard rétroviseur, fenêtre G/D |

---

## Notes techniques

- **Synchronisation** : `SyncClock` basé sur `time.perf_counter()` — précision < 1 ms
- **BLE Windows** : un seul loop asyncio global partagé (évite les conflits WinRT)
- **EEG** : connexion RFCOMM socket direct — pas de dépendance externe
- **Depth** : fallback automatique RGB seul si OpenNI2 non disponible
