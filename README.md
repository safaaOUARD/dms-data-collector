# 🚗 DMS Data Collector

> Prototype multisensoriel de **surveillance du conducteur** (Driver Monitoring System) : une application de bureau qui acquiert, synchronise et enregistre en temps réel les signaux d'un EEG, d'un bracelet connecté, d'une caméra 3D et d'un volant, pour constituer un jeu de données de comportements de conduite.

![Python](https://img.shields.io/badge/Python-3.9+-3776AB?logo=python&logoColor=white)
![PyQt6](https://img.shields.io/badge/UI-PyQt6-41CD52?logo=qt&logoColor=white)
![OpenCV](https://img.shields.io/badge/Vision-OpenCV-5C3EE8?logo=opencv&logoColor=white)
![HDF5](https://img.shields.io/badge/Stockage-HDF5%20%2B%20SQLite-orange)
![Plateforme](https://img.shields.io/badge/Plateforme-Windows-0078D6?logo=windows&logoColor=white)

**Projet de Fin d'Année (PFA)** · ENSA El Jadida (ENSAJ) · Filière ISIC · 2025/2026

## 🎥 Démonstration

▶️ **[Voir la vidéo de démonstration](https://drive.google.com/file/d/1b1Pun9WmvF8Ix4btKmBbW_J_x1_ON580/view?usp=sharing)**

## 📄 Documents du projet

- 📘 [Rapport du PFA (PDF)](docs/Rapport_PFA_DMS.pdf)
- 📊 [Présentation de soutenance (PDF)](docs/PFA%20PPT%20DMS%20ISIC%20-%202%20BLUE.pdf)

## 📌 Contexte et objectif

La distraction et la somnolence sont deux causes majeures d'accidents. Pour entraîner des modèles capables de les détecter, il faut des données **réalistes, multimodales et parfaitement synchronisées**.

Ce projet fournit l'outil d'acquisition : il pilote quatre capteurs en parallèle, horodate chaque mesure avec une horloge commune et enregistre des sessions étiquetées par comportement (distraction, fatigue, attention, regard).

## 🧩 Matériel utilisé

| Capteur | Modèle | Interface | Données produites |
|---|---|---|---|
| EEG | NeuroSky MindWave Mobile 2 | Bluetooth classique (RFCOMM) | Activité cérébrale (13 colonnes) |
| Bracelet connecté | H59MAX iSo Tech | Bluetooth Low Energy (Bleak) | Signaux physiologiques (5 colonnes) |
| Caméra 3D | ORBBEC Astra Pro | USB (OpenCV + OpenNI2) | Vidéo RGB + carte de profondeur |
| Volant | Logitech G29 Driving Force | USB HID | Commandes de conduite à 30 Hz (7 colonnes) |

## ✨ Fonctionnalités

- 🖥️ **Interface PyQt6** : état des capteurs en direct, qualité du signal EEG, gestion des sujets et des sessions
- ⏱️ **Synchronisation précise** des quatre flux grâce à une horloge partagée (`SyncClock`, basée sur `time.perf_counter()`, précision inférieure à 1 ms)
- 🏷️ **Étiquetage des sessions** par classe et par activité (voir ci-dessous)
- 💾 **Stockage structuré** : HDF5 compressé (signaux), CSV (horodatages vidéo et profondeur), MP4/AVI (vidéo), SQLite (métadonnées)
- ✅ **Validation automatique** de chaque session et génération d'un rapport (`session_report.txt`)
- 🔐 **Accès administrateur protégé** par mot de passe
- 🛡️ **Tolérance aux pannes** : bascule automatique sur la vidéo RGB seule si le flux de profondeur (OpenNI2) n'est pas disponible
- 👤 **Anonymisation** : les participants sont identifiés par un `subject_id` généré automatiquement (`subject_001`, `subject_002`...)

## 🏷️ Classes comportementales

| Classe | Activités enregistrées |
|---|---|
| `distraction` | Téléphone (gauche / droite), SMS, boisson, radio, passager |
| `fatigue` | Bâillement, micro-sommeil, conduite somnolente |
| `attentiveness` | Conduite normale |
| `gaze` | Regard vers le rétroviseur, la fenêtre gauche / droite |

## 🏗️ Architecture

```
dms-data-collector/
├── app.py                      # Interface PyQt6 : écran principal
├── acquisition/
│   ├── sync_clock.py           # Horloge partagée entre les capteurs
│   ├── eeg.py                  # NeuroSky MindWave (socket RFCOMM direct)
│   ├── bracelet.py             # H59MAX iSo Tech (BLE)
│   ├── camera.py               # ORBBEC Astra Pro (RGB + profondeur)
│   └── wheel.py                # Logitech G29 (30 Hz)
├── storage/
│   └── session_storage.py      # SQLite + HDF5 + CSV
├── dataset/                    # Données collectées (généré à l'exécution, non publié)
├── docs/                       # Rapport et présentation
└── requirements.txt
```

### Organisation d'une session enregistrée

```
dataset/<classe>/subject_XXX/session_AAAAMMJJ_HHMMSS/
├── eeg.h5                  # Signaux EEG
├── bracelet.h5             # Signaux du bracelet
├── driving.h5              # Données du volant
├── video_rgb.mp4           # Vidéo RGB de conduite
├── timestamps_video.csv    # Horodatages des images RGB
├── depth_timestamps.csv    # Horodatages et profondeur (mm)
└── session_report.txt      # Rapport de validation
```

## 🚀 Installation et lancement

### Prérequis
- **Windows 10 ou 11** (Bluetooth classique et BLE via WinRT)
- Python 3.9 ou supérieur
- Les capteurs listés ci-dessus (l'application démarre aussi si certains sont absents)

### Installation

```bash
git clone https://github.com/safaaOUARD/dms-data-collector.git
cd dms-data-collector
pip install -r requirements.txt
```

### Flux de profondeur (optionnel)

1. Télécharger le SDK OpenNI2 : https://www.orbbec.com/developers/openni-sdk/
2. Décommenter `openni>=2.3.0` dans `requirements.txt`, puis `pip install openni`
3. Si le SDK n'est pas dans `C:\OpenNI2\sdk\libs`, indiquer son emplacement :
```bash
set OPENNI2_LIBS_PATH=C:\chemin\vers\OpenNI2\sdk\libs
```

### Lancement

```bash
set DMS_ADMIN_PASSWORD=votre_mot_de_passe
python app.py
```

## 🔒 Confidentialité des données

Les sessions enregistrées contiennent des signaux physiologiques et des vidéos de participants. **Le jeu de données n'est donc pas publié** dans ce dépôt (dossier `dataset/` vide, base `sessions.db` exclue). Seul le code d'acquisition est partagé.

## 🧠 Compétences mises en pratique

- Acquisition de données multicapteurs en temps réel (Bluetooth classique, BLE, USB, caméra 3D)
- Programmation concurrente : threads, boucle `asyncio` partagée pour BLE sous Windows
- Synchronisation temporelle de flux hétérogènes
- Interface graphique avec PyQt6
- Stockage scientifique : HDF5, SQLite, CSV
- Conception d'un protocole d'expérimentation et respect de l'anonymat des participants

## 🔭 Perspectives

- Entraîner des modèles de détection de distraction et de somnolence sur les données collectées
- Fusionner les modalités (EEG, physiologie, vision, conduite) pour la classification en temps réel
- Étendre le système à d'autres capteurs et à un environnement embarqué

## 👥 Équipe

- Rajae Elkamili
- Safaa Ouard ([@safaaOUARD](https://github.com/safaaOUARD))
- Houda Riad

**Encadrante :** Pr. Asmaa El Hannani
