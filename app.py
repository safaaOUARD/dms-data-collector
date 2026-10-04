"""
DMS Data Collector
Architecture simplifiée :
  - Pas d'inscription / connexion pour les sujets
  - Login unique pour l'admin (mot de passe configurable)
  - subject_id généré automatiquement (subject_001, 002...)
  - Métadonnées génériques collectées dans SetupScreen :
      tranche_age, genre (F/H), port_lunettes, conditions_med
  - Stockage : sessions.db (SQLite) + HDF5 + CSV

Flux :
  AdminLoginScreen → SetupScreen → DashboardScreen → DatasetScreen
"""

import sys, os
from collections import deque
from datetime import datetime

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QFrame, QGridLayout, QStackedWidget,
    QComboBox, QScrollArea, QProgressBar, QTableWidget,
    QTableWidgetItem, QHeaderView, QSlider, QLineEdit,
    QMessageBox, QSizePolicy, QGroupBox
)
from PyQt6.QtCore  import Qt, QTimer, pyqtSignal
from PyQt6.QtGui   import QPainter, QPen, QColor, QLinearGradient, QBrush, QPainterPath

from acquisition.sync_clock  import SyncClock
from acquisition.eeg         import EEGReader
from acquisition.bracelet    import BraceletReader
from acquisition.camera      import CameraReader
from acquisition.wheel       import WheelReader
from storage.session_storage import (
    CLASSES, DATA_ROOT, TRANCHES_AGE, GENRES, LUNETTES,
    init_sessions_db, create_session_path, insert_session,
    validate_hdf5, save_session_report,
    generate_next_subject_id, get_all_subject_ids,
    get_subject_session_count, get_all_sessions, get_sessions_stats,
)

# ── Mot de passe admin ────────────────────────────────────────
ADMIN_PASSWORD = "dms2026"

# ── Palette plus claire et contrastée ───────────────────────────────────
# Version haute visibilité - fond clair, texte foncé
BG      = "#F8F9FA"      # Fond principal blanc cassé
PANEL   = "#FFFFFF"      # Panneaux blancs
PANEL2  = "#F0F2F5"      # Panneaux secondaires gris clair
BORDER  = "#D1D5DB"      # Bordures grises
TEXT    = "#1F2937"      # Texte principal gris foncé
DIM     = "#6B7280"      # Texte secondaire gris
ACCENT  = "#2563EB"      # Bleu vif
GREEN   = "#10B981"      # Vert émeraude
RED     = "#EF4444"      # Rouge vif
YELLOW  = "#F59E0B"      # Jaune/orange
PURPLE  = "#8B5CF6"      # Violet
CYAN    = "#06B6D4"      # Cyan
ORANGE  = "#F97316"      # Orange

C_EEG   = "#8B5CF6"      # Violet
C_BRAC  = "#06B6D4"      # Cyan  
C_WHEEL = "#10B981"      # Vert
C_CAM   = "#F97316"      # Orange

CLASS_COLORS = {
    "distraction":   "#F97316",   # Orange vif
    "fatigue":       "#EF4444",   # Rouge
    "attentiveness": "#10B981",   # Vert
    "gaze":          "#06B6D4",   # Cyan
}

DURATION_MIN = 30
DURATION_MAX = 60
DURATION_DEF = 45

SS = f"""
QWidget {{ background:{BG}; color:{TEXT};
          font-family:'Segoe UI',Arial,sans-serif; font-size:11px; }}
QFrame  {{ background:{PANEL}; border:1px solid {BORDER}; border-radius:8px; }}
QLabel  {{ background:transparent; border:none; }}
QLineEdit {{
    background:{PANEL2}; color:{TEXT}; border:1px solid {BORDER};
    border-radius:6px; padding:8px 12px; font-size:12px;
}}
QLineEdit:focus {{ border:1px solid {ACCENT}; }}
QComboBox {{
    background:{PANEL}; color:{TEXT}; border:1px solid {BORDER};
    border-radius:5px; padding:5px 8px; min-height:28px;
}}
QComboBox:focus {{ border:1px solid {ACCENT}; }}
QComboBox QAbstractItemView {{
    background:{PANEL}; color:{TEXT}; selection-background-color:{ACCENT};
}}
QSlider::groove:horizontal {{
    height:6px; background:{BORDER}; border-radius:3px;
}}
QSlider::handle:horizontal {{
    width:16px; height:16px; margin:-5px 0;
    background:{ACCENT}; border-radius:8px;
}}
QSlider::sub-page:horizontal {{
    background:{ACCENT}; border-radius:3px;
}}
QProgressBar {{
    background:{PANEL}; border:1px solid {BORDER}; border-radius:5px;
    text-align:center; color:{TEXT}; font-weight:bold; height:20px;
}}
QProgressBar::chunk {{ background:{GREEN}; border-radius:4px; }}
QScrollBar:vertical {{ background:{BG}; width:8px; border:none; }}
QScrollBar::handle:vertical {{ background:{BORDER}; border-radius:4px; min-height:20px; }}
QTableWidget {{
    background:{PANEL}; color:{TEXT}; gridline-color:{BORDER};
    border:1px solid {BORDER}; border-radius:6px;
}}
QHeaderView::section {{
    background:{PANEL}; color:{DIM}; padding:6px;
    border:none; border-bottom:1px solid {BORDER}; font-weight:bold;
}}
QTableWidget::item:selected {{ background:#1f3a5f; }}
"""

# ── Helpers UI ────────────────────────────────────────────────
def btn_primary(text, color=GREEN):
    b = QPushButton(text)
    b.setMinimumHeight(42)
    b.setStyleSheet(
        f"background:{color};color:#0E1117;border:none;border-radius:8px;"
        f"font-size:13px;font-weight:bold;"
    )
    return b

def btn_outline(text, color=ACCENT):
    b = QPushButton(text)
    b.setMinimumHeight(38)
    b.setStyleSheet(
        f"background:transparent;color:{color};border:1px solid {color};"
        f"border-radius:8px;font-size:12px;font-weight:bold;"
    )
    return b

def section_lbl(text, color=ACCENT):
    l = QLabel(text)
    l.setStyleSheet(f"color:{color};font-weight:bold;font-size:12px;")
    return l

def combo_styled(items):
    c = QComboBox()
    c.addItems(items)
    return c


# ╔══════════════════════════════════════════╗
# ║  WIDGET — MiniGraph                      ║
# ╚══════════════════════════════════════════╝
class MiniGraph(QWidget):
    def __init__(self, color, max_val=100):
        super().__init__()
        self.color   = QColor(color)
        self.max_val = max_val
        self.data    = deque([0]*60, maxlen=60)
        self.setMinimumHeight(45)
        self.setMaximumHeight(55)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

    def push(self, val):
        self.data.append(val)
        self.update()

    def paintEvent(self, a0):  # type: ignore[override]
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        pts = list(self.data)
        if len(pts) < 2:
            return
        step = w / (len(pts)-1)
        grad = QLinearGradient(0,0,0,h)
        c2 = QColor(self.color); c2.setAlpha(30)
        grad.setColorAt(0,c2); grad.setColorAt(1,QColor(0,0,0,0))
        p.setBrush(QBrush(grad)); p.setPen(Qt.PenStyle.NoPen)
        path = QPainterPath()
        path.moveTo(0, h)
        for i,v in enumerate(pts):
            path.lineTo(i*step, h-(v/self.max_val)*h)
        path.lineTo(w, h); path.closeSubpath()
        p.drawPath(path)
        p.setPen(QPen(self.color, 1.5))
        p.setBrush(Qt.BrushStyle.NoBrush)
        path2 = QPainterPath()
        for i,v in enumerate(pts):
            y = h-(v/self.max_val)*h
            if i==0: path2.moveTo(0,y)
            else:    path2.lineTo(i*step,y)
        p.drawPath(path2)


# ╔══════════════════════════════════════════╗
# ║  WIDGET — DeviceToggle                   ║
# ╚══════════════════════════════════════════╝
class DeviceToggle(QFrame):
    def __init__(self, name, color, detail):
        super().__init__()
        self.name = name
        self.setFixedHeight(80)
        self.setStyleSheet(
            f"background:{PANEL};border:1px solid {BORDER};border-radius:8px;"
        )
        root = QHBoxLayout(self)
        root.setContentsMargins(14,10,14,10); root.setSpacing(12)
        dot = QLabel()
        dot.setFixedSize(10,10)
        dot.setStyleSheet(f"background:{color};border-radius:5px;border:none;")
        root.addWidget(dot)
        col = QVBoxLayout(); col.setSpacing(2)
        ln = QLabel(name); ln.setStyleSheet(f"color:{color};font-weight:bold;font-size:12px;")
        ld = QLabel(detail); ld.setStyleSheet(f"color:{DIM};font-size:9px;")
        col.addWidget(ln); col.addWidget(ld)
        root.addLayout(col); root.addStretch()
        self.lbl_quality = QLabel("Signal OK")
        self.lbl_quality.setVisible(name == "EEG")
        self.lbl_quality.setStyleSheet(
            f"background:#1a3a1a;color:{GREEN};border-radius:4px;"
            f"padding:2px 8px;font-size:9px;font-weight:bold;border:none;"
        )
        root.addWidget(self.lbl_quality)
        self.btn = QPushButton("Simulation")
        self.btn.setFixedSize(110,30)
        self.btn.setCheckable(True)
        self.btn.clicked.connect(self._toggle)
        self._apply_style()
        root.addWidget(self.btn)

    def _toggle(self): self._apply_style()

    def _apply_style(self):
        if self.btn.isChecked():
            self.btn.setText("Connecté")
            self.btn.setStyleSheet(
                f"background:#1a3a1a;color:{GREEN};"
                f"border:1px solid {GREEN};border-radius:5px;font-weight:bold;"
            )
            self.setStyleSheet(
                f"background:{PANEL};border:1px solid {GREEN};border-radius:8px;"
            )
        else:
            self.btn.setText("Simulation")
            self.btn.setStyleSheet(
                f"background:#2a2a1a;color:{YELLOW};"
                f"border:1px solid {YELLOW};border-radius:5px;font-weight:bold;"
            )
            self.setStyleSheet(
                f"background:{PANEL};border:1px solid {BORDER};border-radius:8px;"
            )

    @property
    def is_simulated(self): return not self.btn.isChecked()

    def set_quality(self, ok):
        if self.name != "EEG": return
        if ok:
            self.lbl_quality.setText("Signal OK")
            self.lbl_quality.setStyleSheet(
                f"background:#1a3a1a;color:{GREEN};border-radius:4px;"
                f"padding:2px 8px;font-size:9px;font-weight:bold;border:none;"
            )
        else:
            self.lbl_quality.setText("Signal faible")
            self.lbl_quality.setStyleSheet(
                f"background:#3a1a1a;color:{RED};border-radius:4px;"
                f"padding:2px 8px;font-size:9px;font-weight:bold;border:none;"
            )


# ╔══════════════════════════════════════════╗
# ║  SCREEN 0 — ADMIN LOGIN                  ║
# ╚══════════════════════════════════════════╝
class AdminLoginScreen(QWidget):
    success_signal = pyqtSignal()

    def __init__(self):
        super().__init__()
        self._build()

    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0,0,0,0)
        root.addStretch()

        card = QFrame()
        card.setStyleSheet(
            f"background:{PANEL};border:1px solid {BORDER};border-radius:12px;"
        )
        card.setFixedWidth(400)
        cl = QVBoxLayout(card)
        cl.setContentsMargins(36,36,36,36); cl.setSpacing(18)

        logo = QLabel("DMS")
        logo.setStyleSheet(
            f"color:{ACCENT};font-size:36px;font-weight:bold;letter-spacing:4px;"
        )
        logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sub = QLabel("Data Collector")
        sub.setStyleSheet(f"color:{DIM};font-size:13px;")
        sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sub2 = QLabel("Acces Administrateur")
        sub2.setStyleSheet(f"color:{DIM};font-size:11px;")
        sub2.setAlignment(Qt.AlignmentFlag.AlignCenter)
        cl.addWidget(logo); cl.addWidget(sub); cl.addWidget(sub2)

        sep = QFrame(); sep.setFixedHeight(1)
        sep.setStyleSheet(f"background:{BORDER};border:none;border-radius:0;")
        cl.addWidget(sep)

        lbl = QLabel("Mot de passe")
        lbl.setStyleSheet(f"color:{DIM};font-size:10px;font-weight:bold;")
        self.f_pwd = QLineEdit()
        self.f_pwd.setPlaceholderText("Entrez le mot de passe admin")
        self.f_pwd.setEchoMode(QLineEdit.EchoMode.Password)
        self.f_pwd.returnPressed.connect(self._on_login)
        cl.addWidget(lbl)
        cl.addWidget(self.f_pwd)

        self.lbl_err = QLabel()
        self.lbl_err.setStyleSheet(f"color:{RED};font-size:10px;")
        self.lbl_err.hide()
        cl.addWidget(self.lbl_err)

        btn = btn_primary("Connexion", ACCENT)
        btn.clicked.connect(self._on_login)
        cl.addWidget(btn)

        h = QHBoxLayout(); h.addStretch(); h.addWidget(card); h.addStretch()
        root.addLayout(h)
        root.addStretch()

    def _on_login(self):
        self.lbl_err.hide()
        if self.f_pwd.text() == ADMIN_PASSWORD:
            self.f_pwd.clear()
            self.success_signal.emit()
        else:
            self.lbl_err.setText("Mot de passe incorrect.")
            self.lbl_err.show()
            self.f_pwd.selectAll()


# ╔══════════════════════════════════════════╗
# ║  SCREEN 1 — SETUP                        ║
# ╚══════════════════════════════════════════╝
class SetupScreen(QWidget):
    go_signal     = pyqtSignal(dict)
    logout_signal = pyqtSignal()

    def __init__(self):
        super().__init__()
        self._build()

    def _build(self):
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("border:none;")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0,0,0,0)
        outer.addWidget(scroll)
        content = QWidget()
        scroll.setWidget(content)
        root = QVBoxLayout(content)
        root.setContentsMargins(36,24,36,24); root.setSpacing(18)

        # En-tête
        hdr = QHBoxLayout()
        ttl = QLabel("DMS Data Collector")
        ttl.setStyleSheet(f"color:{TEXT};font-size:20px;font-weight:bold;")
        hdr.addWidget(ttl); hdr.addStretch()
        btn_lo = QPushButton("Deconnexion")
        btn_lo.setStyleSheet(
            f"background:transparent;color:{RED};border:1px solid {RED};"
            f"border-radius:6px;padding:4px 12px;font-size:10px;"
        )
        btn_lo.clicked.connect(self.logout_signal.emit)
        hdr.addWidget(btn_lo)
        root.addLayout(hdr)

        sep = QFrame(); sep.setFixedHeight(1)
        sep.setStyleSheet(f"background:{BORDER};border:none;border-radius:0;")
        root.addWidget(sep)

        # ── BLOC SUJET ────────────────────────────────────────
        root.addWidget(section_lbl("① Sujet"))
        frm_subj = QFrame()
        frm_subj.setStyleSheet(
            f"background:{PANEL};border:1px solid {BORDER};border-radius:8px;"
        )
        gs = QGridLayout(frm_subj)
        gs.setContentsMargins(18,14,18,14); gs.setSpacing(12)

        # subject_id
        gs.addWidget(QLabel("Subject ID :"), 0, 0)
        row_id = QHBoxLayout()
        self.combo_subject = QComboBox()
        self.combo_subject.setMinimumWidth(160)
        self.combo_subject.currentTextChanged.connect(self._on_subject_change)

        self.lbl_new_id = QLabel()
        self.lbl_new_id.setStyleSheet(
            f"color:{ACCENT};border:1px solid {ACCENT};border-radius:6px;"
            f"padding:2px 10px;font-size:10px;font-weight:bold;"
        )

        btn_new = QPushButton("+ Nouveau sujet")
        btn_new.setFixedHeight(28)
        btn_new.setStyleSheet(
            f"background:transparent;color:{GREEN};border:1px solid {GREEN};"
            f"border-radius:5px;padding:0 10px;font-size:10px;font-weight:bold;"
        )
        btn_new.clicked.connect(self._new_subject)

        row_id.addWidget(self.combo_subject)
        row_id.addWidget(self.lbl_new_id)
        row_id.addWidget(btn_new)
        row_id.addStretch()
        gs.addLayout(row_id, 0, 1)

        self.lbl_nb_sessions = QLabel("")
        self.lbl_nb_sessions.setStyleSheet(f"color:{DIM};font-size:10px;")
        gs.addWidget(self.lbl_nb_sessions, 0, 2)

        # Métadonnées génériques
        gs.addWidget(QLabel("Tranche d'age :"), 1, 0)
        self.combo_age = combo_styled(TRANCHES_AGE)
        self.combo_age.setFixedWidth(140)
        gs.addWidget(self.combo_age, 1, 1)

        gs.addWidget(QLabel("Genre :"), 2, 0)
        self.combo_genre = combo_styled(GENRES)
        self.combo_genre.setFixedWidth(140)
        gs.addWidget(self.combo_genre, 2, 1)

        gs.addWidget(QLabel("Port de lunettes :"), 3, 0)
        self.combo_lunettes = combo_styled(LUNETTES)
        self.combo_lunettes.setFixedWidth(200)
        note_lun = QLabel("Impact sur la classification gaze")
        note_lun.setStyleSheet(f"color:{YELLOW};font-size:9px;")
        gs.addWidget(self.combo_lunettes, 3, 1)
        gs.addWidget(note_lun, 3, 2)

        gs.addWidget(QLabel("Conditions medicales :"), 4, 0)
        self.f_cond = QLineEdit()
        self.f_cond.setPlaceholderText("Optionnel — ex: trouble de la vision, epilepsie")
        gs.addWidget(self.f_cond, 4, 1, 1, 2)

        root.addWidget(frm_subj)
        self._refresh_subjects()

        # ── BLOC CLASSE & ACTIVITE ────────────────────────────
        root.addWidget(section_lbl("② Classe de conduite"))
        frm_cls = QFrame()
        frm_cls.setStyleSheet(
            f"background:{PANEL};border:1px solid {BORDER};border-radius:8px;"
        )
        gc = QGridLayout(frm_cls)
        gc.setContentsMargins(18,14,18,14); gc.setSpacing(12)

        gc.addWidget(QLabel("Classe :"), 0, 0)
        self.combo_class = QComboBox()
        self.combo_class.setFixedWidth(200)
        for cls in CLASSES:
            self.combo_class.addItem(cls.capitalize(), cls)
        self.combo_class.currentIndexChanged.connect(self._on_class_change)
        gc.addWidget(self.combo_class, 0, 1)

        gc.addWidget(QLabel("Activite :"), 1, 0)
        self.combo_activity = QComboBox()
        self.combo_activity.setFixedWidth(200)
        gc.addWidget(self.combo_activity, 1, 1)

        self.lbl_badge = QLabel()
        gc.addWidget(self.lbl_badge, 0, 2, 2, 1,
                     Qt.AlignmentFlag.AlignLeft|Qt.AlignmentFlag.AlignVCenter)
        root.addWidget(frm_cls)
        self._on_class_change(0)

        # ── BLOC DUREE ─────────────────────────────────────────
        root.addWidget(section_lbl("③ Duree de session  (30 – 60 s recommande)"))
        frm_dur = QFrame()
        frm_dur.setStyleSheet(
            f"background:{PANEL};border:1px solid {BORDER};border-radius:8px;"
        )
        ld = QVBoxLayout(frm_dur)
        ld.setContentsMargins(18,14,18,14); ld.setSpacing(10)
        row_sl = QHBoxLayout()
        lbl_lo = QLabel(f"{DURATION_MIN}s"); lbl_lo.setStyleSheet(f"color:{DIM};")
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(DURATION_MIN, DURATION_MAX)
        self.slider.setValue(DURATION_DEF)
        self.slider.setTickPosition(QSlider.TickPosition.TicksBelow)
        self.slider.setTickInterval(5)
        self.lbl_dur = QLabel(f"{DURATION_DEF} secondes")
        self.lbl_dur.setStyleSheet(
            f"color:{ACCENT};font-weight:bold;font-size:13px;min-width:110px;"
        )
        self.slider.valueChanged.connect(
            lambda v: self.lbl_dur.setText(f"{v} secondes")
        )
        lbl_hi = QLabel(f"{DURATION_MAX}s"); lbl_hi.setStyleSheet(f"color:{DIM};")
        row_sl.addWidget(lbl_lo); row_sl.addWidget(self.slider)
        row_sl.addWidget(lbl_hi); row_sl.addSpacing(16)
        row_sl.addWidget(self.lbl_dur)
        ld.addLayout(row_sl)
        root.addWidget(frm_dur)

        # ── BLOC CAPTEURS ──────────────────────────────────────
        root.addWidget(section_lbl("④ Capteurs — Connecte / Simulation"))
        self.toggles = {
            "EEG":      DeviceToggle("EEG",     C_EEG,   "NeuroSky MindWave 2  |  TCP 13854  |  1 Hz"),
            "Bracelet": DeviceToggle("Bracelet", C_BRAC,  "iSo Tech  |  BLE  |  1 Hz"),
            "Camera":   DeviceToggle("Camera",   C_CAM,   "Orbbec Astra Pro  |  USB  |  20 fps"),
            "Volant":   DeviceToggle("Volant",   C_WHEEL, "Logitech G29  |  USB HID  |  30 Hz"),
        }
        for t in self.toggles.values():
            root.addWidget(t)

        root.addStretch()

        # ── BOUTON DEMARRER ────────────────────────────────────
        self.btn_start = btn_primary("Demarrer la collecte", GREEN)
        self.btn_start.clicked.connect(self._on_start)
        root.addWidget(self.btn_start)

    # ── Gestion sujets ─────────────────────────────────────────
    def _refresh_subjects(self):
        self.combo_subject.blockSignals(True)
        self.combo_subject.clear()
        subjects = get_all_subject_ids()
        if subjects:
            self.combo_subject.addItems(subjects)
            self.combo_subject.setVisible(True)
            self.lbl_new_id.setVisible(False)
            self._on_subject_change(self.combo_subject.currentText())
        else:
            next_id = generate_next_subject_id()
            self.lbl_new_id.setText(f"  {next_id}  ")
            self.lbl_new_id.setVisible(True)
            self.combo_subject.setVisible(False)
            self.lbl_nb_sessions.setText("Nouveau sujet")
        self.combo_subject.blockSignals(False)

    def _on_subject_change(self, subject_id):
        if not subject_id:
            return
        n = get_subject_session_count(subject_id)
        self.lbl_nb_sessions.setText(
            f"{n} session{'s' if n!=1 else ''} deja enregistree{'s' if n!=1 else ''}"
            if n > 0 else "Aucune session pour ce sujet"
        )
        self.lbl_new_id.setVisible(False)
        self.combo_subject.setVisible(True)

    def _new_subject(self):
        next_id = generate_next_subject_id()
        self.combo_subject.setVisible(False)
        self.lbl_new_id.setText(f"  {next_id}  ")
        self.lbl_new_id.setVisible(True)
        self.lbl_nb_sessions.setText("Nouveau sujet")

    def _current_subject_id(self) -> str:
        if self.lbl_new_id.isVisible():
            return self.lbl_new_id.text().strip()
        return self.combo_subject.currentText()

    # ── Classe ──────────────────────────────────────────────────
    def _on_class_change(self, _=None):
        cls = self.combo_class.currentData()
        self.combo_activity.clear()
        self.combo_activity.addItems(CLASSES.get(cls, []))
        color = CLASS_COLORS.get(cls, ACCENT)
        self.lbl_badge.setText(f"  {cls.upper()}  ")
        self.lbl_badge.setStyleSheet(
            f"background:transparent;color:{color};"
            f"border:1px solid {color};border-radius:10px;"
            f"padding:3px 14px;font-size:10px;font-weight:bold;"
        )

    # ── Demarrer ────────────────────────────────────────────────
    def _on_start(self):
        subject_id = self._current_subject_id()
        if not subject_id:
            QMessageBox.warning(self, "Sujet manquant",
                "Veuillez selectionner ou creer un sujet.")
            return

        cls      = self.combo_class.currentData()
        activity = self.combo_activity.currentText()
        dur      = self.slider.value()
        devs     = {
            "EEG":      "simulation" if self.toggles["EEG"].is_simulated      else "connected",
            "Bracelet": "simulation" if self.toggles["Bracelet"].is_simulated  else "connected",
            "Camera":   "simulation" if self.toggles["Camera"].is_simulated    else "connected",
            "Volant":   "simulation" if self.toggles["Volant"].is_simulated    else "connected",
        }
        sims = sum(1 for v in devs.values() if v == "simulation")

        # Confirmation
        msg = QMessageBox(self)
        msg.setWindowTitle("Confirmation")
        msg.setStyleSheet(
            f"QMessageBox{{background:{PANEL};color:{TEXT};}}"
            f"QLabel{{color:{TEXT};font-size:11px;}}"
            f"QPushButton{{background:{PANEL};color:{TEXT};"
            f"border:1px solid {BORDER};border-radius:5px;"
            f"padding:6px 18px;font-weight:bold;}}"
        )
        msg.setText(
            f"<b style='color:{ACCENT}'>Recapitulatif de la session</b><br><br>"
            f"<b>Sujet :</b> {subject_id}<br>"
            f"<b>Age :</b> {self.combo_age.currentText()}&nbsp;&nbsp;"
            f"<b>Genre :</b> {self.combo_genre.currentText()}<br>"
            f"<b>Lunettes :</b> {self.combo_lunettes.currentText()}<br>"
            f"<b>Classe :</b> {cls.capitalize()} — {activity}<br>"
            f"<b>Duree :</b> {dur} secondes<br>"
            f"<b>Capteurs en simulation :</b> {sims} / 4<br><br>"
            f"Confirmez-vous le lancement ?"
        )
        msg.setStandardButtons(
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel
        )
        btn_yes = msg.button(QMessageBox.StandardButton.Yes)
        btn_cancel = msg.button(QMessageBox.StandardButton.Cancel)
        if btn_yes: btn_yes.setText("Demarrer")
        if btn_cancel: btn_cancel.setText("Annuler")
        if msg.exec() != QMessageBox.StandardButton.Yes:
            return

        self.go_signal.emit({
            "subject_id":     subject_id,
            "tranche_age":    self.combo_age.currentText(),
            "genre":          self.combo_genre.currentText(),
            "port_lunettes":  self.combo_lunettes.currentText(),
            "conditions_med": self.f_cond.text().strip(),
            "label_class":    cls,
            "label_activity": activity,
            "duration_s":     dur,
            "devices":        devs,
        })

    def refresh(self):
        self._refresh_subjects()


# ╔══════════════════════════════════════════╗
# ║  SCREEN 2 — DASHBOARD                    ║
# ╚══════════════════════════════════════════╝
class DashboardScreen(QWidget):
    stop_signal    = pyqtSignal()
    restart_signal = pyqtSignal()
    dataset_signal = pyqtSignal()
    folder_signal  = pyqtSignal()

    def __init__(self):
        super().__init__()
        self._elapsed  = 0
        self._duration = DURATION_DEF
        self._timer    = QTimer()
        self._timer.timeout.connect(self._tick)
        self._build()

    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(28,20,28,20); root.setSpacing(14)

        hdr = QHBoxLayout()
        self.lbl_title = QLabel("Session en cours")
        self.lbl_title.setStyleSheet(f"color:{TEXT};font-size:18px;font-weight:bold;")
        hdr.addWidget(self.lbl_title); hdr.addStretch()
        self.badge_class    = self._badge("—", ACCENT)
        self.badge_activity = self._badge("—", GREEN)
        self.badge_storage  = self._badge("HDF5 + SQLite", PURPLE)
        for b in [self.badge_class, self.badge_activity, self.badge_storage]:
            hdr.addWidget(b); hdr.addSpacing(6)
        root.addLayout(hdr)

        frm_t = QFrame()
        lt = QVBoxLayout(frm_t); lt.setContentsMargins(20,14,20,14); lt.setSpacing(8)
        row_cd = QHBoxLayout(); row_cd.addStretch()
        self.lbl_timer = QLabel("00:00")
        self.lbl_timer.setStyleSheet(
            f"color:{TEXT};font-size:48px;font-weight:bold;font-family:Consolas,monospace;"
        )
        self.lbl_total = QLabel("/ 00:00")
        self.lbl_total.setStyleSheet(
            f"color:{DIM};font-size:16px;font-weight:bold;margin-top:18px;"
        )
        row_cd.addWidget(self.lbl_timer); row_cd.addSpacing(8)
        row_cd.addWidget(self.lbl_total); row_cd.addStretch()
        lt.addLayout(row_cd)
        self.progress = QProgressBar()
        self.progress.setRange(0,100); self.progress.setValue(0)
        self.progress.setFormat("%p%")
        lt.addWidget(self.progress)
        self.lbl_status = QLabel("En attente...")
        self.lbl_status.setStyleSheet(f"color:{DIM};font-size:10px;")
        self.lbl_status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lt.addWidget(self.lbl_status)
        root.addWidget(frm_t)

        grid = QGridLayout(); grid.setSpacing(10)
        self._graphs = {}; self._val_lbls = {}
        cards = [
            ("EEG",      "Attention",   C_EEG,   100, 0, 0),
            ("EEG",      "Meditation",  C_EEG,   100, 0, 1),
            ("Bracelet", "Freq. card.", C_BRAC,  200, 0, 2),
            ("Bracelet", "SpO2",        C_BRAC,  100, 0, 3),
            ("Volant",   "Steering",    C_WHEEL,   1, 1, 0),
            ("Volant",   "Throttle",    C_WHEEL,   1, 1, 1),
            ("Camera",   "Frames",      C_CAM,  9999, 1, 2),
            ("Sync",     "elapsed_s",   ACCENT, 9999, 1, 3),
        ]
        for dev, sig, color, mx, row, col in cards:
            frm = QFrame()
            frm.setStyleSheet(
                f"background:{PANEL};border:1px solid {BORDER};border-radius:8px;"
            )
            bl = QVBoxLayout(frm); bl.setContentsMargins(10,8,10,6); bl.setSpacing(2)
            ld = QLabel(dev); ld.setStyleSheet(f"color:{color};font-size:9px;font-weight:bold;")
            lv = QLabel("—"); lv.setStyleSheet(f"color:{TEXT};font-size:18px;font-weight:bold;")
            ln = QLabel(sig); ln.setStyleSheet(f"color:{DIM};font-size:9px;")
            g  = MiniGraph(color, max_val=max(mx,1))
            bl.addWidget(ld); bl.addWidget(lv); bl.addWidget(ln); bl.addWidget(g)
            grid.addWidget(frm, row, col)
            self._val_lbls[sig] = lv; self._graphs[sig] = g
        root.addLayout(grid)

        row_btn = QHBoxLayout(); row_btn.setSpacing(10)
        self.btn_stop = QPushButton("Arreter")
        self.btn_stop.setMinimumHeight(40)
        self.btn_stop.setStyleSheet(
            f"background:{RED};color:white;border:none;border-radius:6px;"
            f"font-weight:bold;font-size:12px;"
        )
        self.btn_stop.clicked.connect(self._on_stop)
        self.btn_restart = QPushButton("Recommencer")
        self.btn_restart.setMinimumHeight(40)
        self.btn_restart.setEnabled(False)
        self.btn_restart.setStyleSheet(
            f"background:{YELLOW};color:#0E1117;border:none;border-radius:6px;"
            f"font-weight:bold;font-size:12px;"
        )
        self.btn_restart.clicked.connect(self.restart_signal.emit)
        self.btn_dataset = btn_outline("Voir le Dataset", ACCENT)
        self.btn_dataset.setMinimumHeight(40)
        self.btn_dataset.setEnabled(False)
        self.btn_dataset.clicked.connect(self.dataset_signal.emit)
        self.btn_folder = btn_outline("Ouvrir le dossier", CYAN)
        self.btn_folder.setMinimumHeight(40)
        self.btn_folder.setEnabled(False)
        self.btn_folder.clicked.connect(self.folder_signal.emit)
        row_btn.addWidget(self.btn_stop); row_btn.addWidget(self.btn_restart)
        row_btn.addStretch()
        row_btn.addWidget(self.btn_folder)
        row_btn.addWidget(self.btn_dataset)
        root.addLayout(row_btn)

    def _badge(self, text, color):
        lbl = QLabel(f"  {text}  ")
        lbl.setStyleSheet(
            f"color:{color};border:1px solid {color};"
            f"border-radius:10px;padding:2px 8px;font-size:10px;font-weight:bold;"
        )
        return lbl

    def _fmt(self, s): return f"{s//60:02d}:{s%60:02d}"

    def start_session(self, config):
        self._elapsed = 0; self._duration = config["duration_s"]
        self.lbl_title.setText(f"Session — {config['subject_id']}")
        color = CLASS_COLORS.get(config["label_class"], ACCENT)
        self.badge_class.setText(f"  {config['label_class'].upper()}  ")
        self.badge_class.setStyleSheet(
            f"color:{color};border:1px solid {color};"
            f"border-radius:10px;padding:2px 8px;font-size:10px;font-weight:bold;"
        )
        self.badge_activity.setText(f"  {config['label_activity']}  ")
        self.lbl_total.setText(f"/ {self._fmt(self._duration)}")
        self.lbl_timer.setText("00:00")
        self.lbl_timer.setStyleSheet(
            f"color:{TEXT};font-size:48px;font-weight:bold;font-family:Consolas,monospace;"
        )
        self.progress.setValue(0)
        self.progress.setStyleSheet(
            f"QProgressBar::chunk{{background:{GREEN};border-radius:4px;}}"
        )
        self.lbl_status.setText("Collecte en cours — ecriture HDF5...")
        self.btn_stop.setEnabled(True)
        self.btn_restart.setEnabled(False)
        self.btn_dataset.setEnabled(False)
        self.btn_folder.setEnabled(False)
        for g in self._graphs.values():
            g.data.clear(); g.data.extend([0]*60)
        self._timer.start(1000)

    def _tick(self):
        self._elapsed += 1
        pct = int(self._elapsed / self._duration * 100)
        self.progress.setValue(min(pct,100))
        self.lbl_timer.setText(self._fmt(self._elapsed))
        if self._duration - self._elapsed <= 10:
            self.lbl_timer.setStyleSheet(
                f"color:{RED};font-size:48px;font-weight:bold;font-family:Consolas,monospace;"
            )
            self.progress.setStyleSheet(
                f"QProgressBar::chunk{{background:{RED};border-radius:4px;}}"
            )
        if self._elapsed >= self._duration:
            self._timer.stop()
            self.lbl_status.setText("Durée atteinte — arrêt automatique.")
            self.stop_signal.emit()

    def _on_stop(self):
        self._timer.stop()
        self.btn_stop.setEnabled(False)
        self.lbl_status.setText("Sauvegarde HDF5 + SQLite en cours...")
        self.stop_signal.emit()

    def update_signal(self, sig, val):
        if sig in self._val_lbls:
            self._val_lbls[sig].setText(str(val))
        if sig in self._graphs:
            try:
                self._graphs[sig].push(
                    float(str(val).replace(" s","").replace(" bpm","").replace(" %",""))
                )
            except ValueError:
                pass

    def on_session_saved(self, validation):
        self.btn_restart.setEnabled(True)
        self.btn_dataset.setEnabled(True)
        self.btn_folder.setEnabled(True)
        ok = validation.get("_global_ok", False)
        if ok:
            self.lbl_status.setText("Session validée — HDF5 + SQLite sauvegardés.")
            self.lbl_status.setStyleSheet(f"color:{GREEN};font-size:10px;")
        else:
            problems = [k for k,v in validation.items()
                        if k not in ("_global_ok","_row_counts") and not v.get("ok")]
            self.lbl_status.setText(f"Attention — probleme sur : {', '.join(problems)}")
            self.lbl_status.setStyleSheet(f"color:{YELLOW};font-size:10px;")


# ╔══════════════════════════════════════════╗
# ║  SCREEN 3 — DATASET                      ║
# ╚══════════════════════════════════════════╝
class DatasetScreen(QWidget):
    back_signal = pyqtSignal()

    def __init__(self):
        super().__init__()
        self._build()

    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(28,20,28,20); root.setSpacing(14)

        hdr = QHBoxLayout()
        ttl = QLabel("Dataset")
        ttl.setStyleSheet(f"color:{TEXT};font-size:18px;font-weight:bold;")
        hdr.addWidget(ttl); hdr.addStretch()
        lbl_src = QLabel("  Source : sessions.db (SQLite)  ")
        lbl_src.setStyleSheet(
            f"color:{ACCENT};border:1px solid {ACCENT};"
            f"border-radius:8px;padding:2px 8px;font-size:9px;font-weight:bold;"
        )
        hdr.addWidget(lbl_src); hdr.addSpacing(10)
        self.lbl_total = QLabel()
        self.lbl_total.setStyleSheet(f"color:{DIM};font-size:10px;")
        hdr.addWidget(self.lbl_total); hdr.addSpacing(16)
        btn_back = btn_outline("Nouvelle session", ACCENT)
        btn_back.clicked.connect(self.back_signal.emit)
        hdr.addWidget(btn_back)
        root.addLayout(hdr)

        # Stats par classe
        self.frm_stats = QFrame()
        self.frm_stats.setStyleSheet(
            f"background:{PANEL};border:1px solid {BORDER};border-radius:8px;"
        )
        self.lay_stats = QHBoxLayout(self.frm_stats)
        self.lay_stats.setContentsMargins(16,12,16,12); self.lay_stats.setSpacing(12)
        root.addWidget(self.frm_stats)

        # Tableau
        self.table = QTableWidget()
        self.table.setColumnCount(10)
        self.table.setHorizontalHeaderLabels([
            "Subject ID", "Age", "Genre", "Lunettes",
            "Classe", "Activite", "Duree reelle",
            "Lignes EEG", "Validation", "Session ID"
        ])
        header = self.table.horizontalHeader()
        if header:
            header.setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.doubleClicked.connect(self._open_session_folder)
        self.table.setToolTip("Double-clic sur une session pour ouvrir son dossier")
        root.addWidget(self.table)

        row_bot = QHBoxLayout()
        self.btn_refresh = btn_outline("Actualiser", ACCENT)
        self.btn_refresh.clicked.connect(self.refresh)
        row_bot.addWidget(self.btn_refresh); row_bot.addStretch()
        root.addLayout(row_bot)

    def _open_session_folder(self, index):
        """Ouvre le dossier de la session sélectionnée dans l'explorateur Windows."""
        import subprocess, os
        row = index.row()
        sessions = get_all_sessions()
        if row < len(sessions):
            path = sessions[row].get("session_path", "")
            if path and os.path.exists(path):
                subprocess.Popen(f'explorer "{os.path.abspath(path)}"')
            else:
                # Fallback : ouvrir le dossier dataset/
                subprocess.Popen('explorer dataset')

    def refresh(self):
        for i in reversed(range(self.lay_stats.count())):
            item = self.lay_stats.itemAt(i)
            if item:
                w = item.widget()
                if w: w.deleteLater()

        stats = get_sessions_stats()
        total = sum(stats.values())
        self.lbl_total.setText(
            f"{total} session{'s' if total!=1 else ''} au total"
        )

        for cls, cnt in stats.items():
            color = CLASS_COLORS.get(cls, ACCENT)
            box = QFrame()
            box.setStyleSheet(
                f"background:#0E1117;border:1px solid {color};border-radius:6px;"
            )
            bl = QVBoxLayout(box); bl.setContentsMargins(14,8,14,8); bl.setSpacing(2)
            lv = QLabel(str(cnt))
            lv.setStyleSheet(f"color:{color};font-size:24px;font-weight:bold;")
            lc = QLabel(cls.capitalize())
            lc.setStyleSheet(f"color:{DIM};font-size:9px;")
            bl.addWidget(lv); bl.addWidget(lc)
            self.lay_stats.addWidget(box)

        sessions = get_all_sessions()
        self.table.setRowCount(len(sessions))
        for i, s in enumerate(sessions):
            ok = s.get("validation_ok", 0)
            self.table.setItem(i, 0, QTableWidgetItem(s["subject_id"]))
            self.table.setItem(i, 1, QTableWidgetItem(s.get("tranche_age","—")))
            self.table.setItem(i, 2, QTableWidgetItem(s.get("genre","—")))
            self.table.setItem(i, 3, QTableWidgetItem(s.get("port_lunettes","—")))
            self.table.setItem(i, 4, QTableWidgetItem(s["label_class"]))
            self.table.setItem(i, 5, QTableWidgetItem(s["label_activity"]))
            self.table.setItem(i, 6, QTableWidgetItem(
                f"{s['duration_real_s']:.1f} s" if s.get('duration_real_s') else "—"
            ))
            self.table.setItem(i, 7, QTableWidgetItem(str(s["eeg_rows"])))
            # Validation — sans emoji pour compatibilite Windows
            vi = QTableWidgetItem("OK" if ok else "Attention")
            vi.setForeground(QColor(GREEN if ok else YELLOW))
            self.table.setItem(i, 8, vi)
            self.table.setItem(i, 9, QTableWidgetItem(s["session_id"]))


# ╔══════════════════════════════════════════╗
# ║  MAIN WINDOW                             ║
# ╚══════════════════════════════════════════╝
class MainWindow(QMainWindow):

    def __init__(self):
        super().__init__()
        self.setWindowTitle("DMS Data Collector")
        self.setMinimumSize(980, 700)
        self.setStyleSheet(SS)

        init_sessions_db()

        self._readers      = []
        self._config       = {}
        self._session_path = None
        self._session_id   = None

        self.stack = QStackedWidget()
        self.setCentralWidget(self.stack)

        self.scr_login   = AdminLoginScreen()
        self.scr_setup   = SetupScreen()
        self.scr_dash    = DashboardScreen()
        self.scr_dataset = DatasetScreen()

        for s in [self.scr_login, self.scr_setup, self.scr_dash, self.scr_dataset]:
            self.stack.addWidget(s)

        # Connexions
        self.scr_login.success_signal.connect(
            lambda: self.stack.setCurrentWidget(self.scr_setup)
        )
        self.scr_setup.go_signal.connect(self._start_session)
        self.scr_setup.logout_signal.connect(
            lambda: self.stack.setCurrentWidget(self.scr_login)
        )
        self.scr_dash.stop_signal.connect(self._stop_session)
        self.scr_dash.restart_signal.connect(
            lambda: self._start_session(self._config)
        )
        self.scr_dash.dataset_signal.connect(self._open_dataset)
        self.scr_dash.folder_signal.connect(self._open_session_folder)
        self.scr_dataset.back_signal.connect(self._back_to_setup)

        self._upd_timer = QTimer()
        self._upd_timer.timeout.connect(self._update_dashboard)

        self.stack.setCurrentWidget(self.scr_login)

    # Dans app.py — méthode _start_session
# Remplace le bloc de démarrage des readers par ceci :

    def _start_session(self, config: dict):
        self._config = config
        os.makedirs(DATA_ROOT, exist_ok=True)

        self._session_path, self._session_id = create_session_path(
            config["label_class"], config["subject_id"]
        )
        SyncClock.start()

        label = config["label_activity"]
        devs  = config["devices"]
        self._readers = [
            EEGReader     (self._session_path, label, devs["EEG"]      == "simulation"),
            BraceletReader(self._session_path, label, devs["Bracelet"] == "simulation"),
            CameraReader  (self._session_path, label, devs["Camera"]   == "simulation"),
            WheelReader   (self._session_path, label, devs["Volant"]   == "simulation"),
        ]

        # ── Ordre de démarrage optimisé ───────────────────────────────────
        # 1. Bracelet BLE en premier — évite conflit Bluetooth avec l'EEG
        brac = next(r for r in self._readers if isinstance(r, BraceletReader))
        brac_sim = devs["Bracelet"] == "simulation"
        print("[App] Démarrage bracelet...")
        brac.start()

        # 2. Si mode réel : attendre la 1ère mesure (max 15s) avant de
        #    lancer les autres readers pour laisser le stack WinRT BLE
        #    se stabiliser.
        if not brac_sim:
            import time as _t
            deadline = _t.time() + 15
            while _t.time() < deadline:
                if brac.last["heart_rate"] > 0:
                    print("[App] Bracelet prêt ✅")
                    break
                _t.sleep(0.5)
            else:
                print("[App] Bracelet pas encore prêt — on continue quand même")

        # 3. Démarrer tous les autres readers
        for r in self._readers:
            if not isinstance(r, BraceletReader):
                r.start()

        # 4. Afficher le dashboard et démarrer le timer de mise à jour
        self.scr_dash.start_session(config)
        self.stack.setCurrentWidget(self.scr_dash)
        self._upd_timer.start(1000)

    def _stop_session(self):
        self._upd_timer.stop()
        duration_real = SyncClock.elapsed()

        for r in self._readers:
            r.stop()
        self._readers = []

        if self._session_path:
            validation = validate_hdf5(self._session_path)
            save_session_report(self._session_path, validation, duration_real)
            rc = validation.get("_row_counts", {})

            insert_session(
                session_id     = self._session_id,
                subject_id     = self._config["subject_id"],
                tranche_age    = self._config.get("tranche_age", ""),
                genre          = self._config.get("genre", ""),
                port_lunettes  = self._config.get("port_lunettes", ""),
                conditions_med = self._config.get("conditions_med", ""),
                label_class    = self._config["label_class"],
                label_activity = self._config["label_activity"],
                duration_s     = self._config["duration_s"],
                duration_real  = duration_real,
                devices        = self._config["devices"],
                session_path   = self._session_path,
                eeg_rows       = rc.get("eeg", 0),
                bracelet_rows  = rc.get("bracelet", 0),
                driving_rows   = rc.get("driving", 0),
                camera_rows    = rc.get("camera", 0),
                depth_rows     = rc.get("depth", 0),
                validation_ok  = validation["_global_ok"],
            )
            self.scr_dash.on_session_saved(validation)

        SyncClock.reset()

    def _open_dataset(self):
        self.scr_dataset.refresh()
        self.stack.setCurrentWidget(self.scr_dataset)

    def _open_session_folder(self):
        """Ouvre le dossier de la dernière session dans l'explorateur Windows."""
        import subprocess, os
        path = self._session_path
        if path and os.path.exists(path):
            subprocess.Popen(f'explorer "{os.path.abspath(path)}"')
        else:
            subprocess.Popen('explorer dataset')

    def _back_to_setup(self):
        self.scr_setup.refresh()
        self.stack.setCurrentWidget(self.scr_setup)

    # ── Dashboard ──────────────────────────────────────────────
    def _update_dashboard(self):
        eeg_r = next((r for r in self._readers if isinstance(r, EEGReader)), None)
        brc_r = next((r for r in self._readers if isinstance(r, BraceletReader)), None)
        whl_r = next((r for r in self._readers if isinstance(r, WheelReader)), None)
        cam_r = next((r for r in self._readers if isinstance(r, CameraReader)), None)

        if eeg_r:
            self.scr_dash.update_signal("Attention",  eeg_r.last["attention"])
            self.scr_dash.update_signal("Meditation", eeg_r.last["meditation"])
            self.scr_setup.toggles["EEG"].set_quality(
                eeg_r.last["signal_quality"] == 0
            )
        if brc_r:
            self.scr_dash.update_signal("Freq. card.", f"{brc_r.last['heart_rate']} bpm")
            self.scr_dash.update_signal("SpO2",        f"{brc_r.last['spo2']} %")
        if whl_r:
            self.scr_dash.update_signal("Steering", f"{whl_r.last['steering']:.2f}")
            self.scr_dash.update_signal("Throttle",  f"{whl_r.last['throttle']:.2f}")
        if cam_r:
            depth_mm = cam_r.last.get("depth_mm", 0)
            cam_display = (f"{cam_r.last['frames']} fr | {int(depth_mm)} mm"
                           if depth_mm > 0 else str(cam_r.last['frames']))
            self.scr_dash.update_signal("Frames", cam_display)
            depth_mm = cam_r.last.get("depth_mm", 0)
            if depth_mm > 0:
                self.scr_dash.update_signal("Frames",
                                            f"{cam_r.last['frames']} fr | {int(depth_mm)} mm")
        self.scr_dash.update_signal("elapsed_s", f"{SyncClock.elapsed():.1f} s")


# ── Entry point ───────────────────────────────────────────────
if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setApplicationName("DMS Data Collector")
    w = MainWindow()
    w.show()
    sys.exit(app.exec())