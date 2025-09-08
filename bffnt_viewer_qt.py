#!/usr/bin/env python3
import os
import sys
import json

try:
    from PySide6 import QtCore, QtGui, QtWidgets
    PYSIDE = True
except Exception:
    try:
        from PyQt5 import QtCore, QtGui, QtWidgets
        PYSIDE = False
    except Exception as ex:
        sys.stderr.write("ПОМИЛКА: потрібен PySide6 або PyQt5: pip install PySide6\n")
        sys.exit(2)


class ImageView(QtWidgets.QGraphicsView):
    clicked = QtCore.Signal(QtCore.QPointF) if PYSIDE else QtCore.pyqtSignal(QtCore.QPointF)
    scaleChanged = QtCore.Signal(float) if PYSIDE else QtCore.pyqtSignal(float)

    def __init__(self, parent=None, use_opengl=True):
        super().__init__(parent)
        if use_opengl:
            try:
                self.setViewport(QtWidgets.QOpenGLWidget())
            except Exception:
                pass
        self.setRenderHint(QtGui.QPainter.Antialiasing, False)
        self.setRenderHint(QtGui.QPainter.SmoothPixmapTransform, True)
        self.setDragMode(QtWidgets.QGraphicsView.NoDrag)
        self.setTransformationAnchor(QtWidgets.QGraphicsView.AnchorUnderMouse)
        # panning state (middle mouse)
        self._panning = False
        self._last_pos = None
        # view scale management
        self._scale = 1.0
        self._scale_min = 0.1
        self._scale_max = 10.0

    def set_scale(self, val: float):
        val = max(self._scale_min, min(self._scale_max, float(val)))
        self._scale = val
        t = QtGui.QTransform()
        t.scale(val, val)
        self.setTransform(t)
        self.scaleChanged.emit(val)

    def mousePressEvent(self, event: QtGui.QMouseEvent):
        if event.button() == QtCore.Qt.MiddleButton:
            self._panning = True
            self._last_pos = (event.position().toPoint() if PYSIDE else event.pos())
            self.setCursor(QtCore.Qt.ClosedHandCursor)
            event.accept()
            return
        if event.button() == QtCore.Qt.LeftButton:
            pos = (event.position().toPoint() if PYSIDE else event.pos())
            p = self.mapToScene(pos)
            self.clicked.emit(p)
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QtGui.QMouseEvent):
        if self._panning and self._last_pos is not None:
            pos = (event.position().toPoint() if PYSIDE else event.pos())
            delta = pos - self._last_pos
            self._last_pos = pos
            self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - delta.x())
            self.verticalScrollBar().setValue(self.verticalScrollBar().value() - delta.y())
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QtGui.QMouseEvent):
        if event.button() == QtCore.Qt.MiddleButton:
            self._panning = False
            self._last_pos = None
            self.unsetCursor()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def wheelEvent(self, event: QtGui.QWheelEvent):
        delta = event.angleDelta().y()
        if delta == 0:
            super().wheelEvent(event)
            return
        factor = 1.2 if delta > 0 else 1 / 1.2
        self.set_scale(self._scale * factor)
        event.accept()
        # do not call super to avoid default scroll


class GridItem(QtWidgets.QGraphicsItem):
    def __init__(self, cw: int, ch: int, rows: int, cols: int, parent=None):
        super().__init__(parent)
        self.cw = int(cw)
        self.ch = int(ch)
        self.rows = int(rows)
        self.cols = int(cols)
        self.x_off = 1
        self.y_off = 1
        self.real_w = self.cw + 1
        self.real_h = self.ch + 1
        self.pen = QtGui.QPen(QtGui.QColor('#00AA00'))
        self.pen.setCosmetic(True)

    def boundingRect(self) -> QtCore.QRectF:
        w = self.rows * self.real_w + self.x_off
        h = self.cols * self.real_h + self.y_off
        return QtCore.QRectF(0, 0, w + 1, h + 1)

    def paint(self, painter: QtGui.QPainter, option, widget=None):
        painter.setPen(self.pen)
        # vertical lines
        for gx in range(self.rows + 1):
            x = gx * self.real_w + self.x_off
            painter.drawLine(x, self.y_off, x, self.cols * self.real_h + self.y_off)
        # horizontal lines
        for gy in range(self.cols + 1):
            y = gy * self.real_h + self.y_off
            painter.drawLine(self.x_off, y, self.rows * self.real_w + self.x_off, y)


class BffntQtViewer(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle('BFFNT Viewer (Qt)')
        self.resize(1280, 840)
        self.settings = QtCore.QSettings('SwitchToolbox', 'BFFNTViewerQt')

        # state
        self.folder = ''
        self.meta = {}
        self.tglp = {}
        self.sheet_png = []
        self.glyphs = []
        self.index_to_glyphs = {}
        self.per_sheet = 0
        self.cw = self.ch = self.rows = self.cols = 0
        self.real_w = self.real_h = 0
        self.current_png = ''
        self.current_sheet_index = 0
        self.selected_cell = None  # (gx, gy)
        self.flip_y = False
        self.rotate_q = 0  # 0..3 quarter turns
        self.orig_img = None
        self.orig_has_alpha = False
        self._dirty = False  # незбережені зміни для поточної комірки

        # central layout
        central = QtWidgets.QWidget()
        self.setCentralWidget(central)
        hbox = QtWidgets.QHBoxLayout(central)
        hbox.setContentsMargins(6, 6, 6, 6)
        hbox.setSpacing(6)

        # left: list
        left_box = QtWidgets.QVBoxLayout()
        self.btn_choose = QtWidgets.QPushButton('Вибрати теку…')
        self.btn_choose.clicked.connect(self.choose_folder)
        left_box.addWidget(self.btn_choose)
        self.list_png = QtWidgets.QListWidget()
        self.list_png.setToolTip('Аркуші шрифту (sheet_*.png). Оберіть аркуш для перегляду.')
        self.list_png.currentRowChanged.connect(self.on_select_png_row)
        left_box.addWidget(self.list_png, 1)
        left_wrap = QtWidgets.QWidget()
        left_wrap.setLayout(left_box)
        left_wrap.setFixedWidth(260)
        hbox.addWidget(left_wrap)

        # center: view + toolbar
        center_box = QtWidgets.QVBoxLayout()
        toolbar = QtWidgets.QHBoxLayout()
        lab_scale = QtWidgets.QLabel('Масштаб:')
        lab_scale.setToolTip('Масштаб перегляду аркуша. Колесо миші змінює масштаб навколо курсора.')
        toolbar.addWidget(lab_scale)
        self.scale_spin = QtWidgets.QDoubleSpinBox()
        self.scale_spin.setToolTip('Масштаб перегляду. Колесо миші також змінює масштаб.')
        self.scale_spin.setRange(0.25, 6.0)
        self.scale_spin.setSingleStep(0.25)
        self.scale_spin.setValue(1.0)
        self.scale_spin.valueChanged.connect(self.on_scale_changed)
        toolbar.addWidget(self.scale_spin)
        self.btn_flip = QtWidgets.QPushButton('Flip Y')
        self.btn_flip.setToolTip('Віддзеркалити зображення по вертикалі (лише перегляд).')
        self.btn_flip.clicked.connect(self.toggle_flip_y)
        toolbar.addWidget(self.btn_flip)
        self.btn_rot = QtWidgets.QPushButton('Rotate 90°')
        self.btn_rot.setToolTip('Повернути зображення на 90° (лише перегляд).')
        self.btn_rot.clicked.connect(self.rotate_90)
        toolbar.addWidget(self.btn_rot)
        toolbar.addStretch(1)
        center_box.addLayout(toolbar)

        self.scene = QtWidgets.QGraphicsScene(self)
        self.view = ImageView(use_opengl=True)
        self.view.setScene(self.scene)
        self.view.clicked.connect(self.on_view_clicked)
        self.view.scaleChanged.connect(self.on_view_scale_changed)
        self.view.setToolTip('ЛКМ — вибір комірки. Середня кнопка — панорамування. Колесо — масштаб. Ctrl+Стрілки — навігація по комірках.')
        center_box.addWidget(self.view, 1)
        # capture mouse events on the viewport for drag handles
        self.view.viewport().installEventFilter(self)
        # capture key events on the view to override default scrolling
        self.view.installEventFilter(self)

        # Global key event filter to catch Ctrl+Arrows anywhere in the app
        try:
            app = QtWidgets.QApplication.instance()
            if app is not None:
                app.installEventFilter(self)
        except Exception:
            pass

        # Global shortcuts (work even when inputs are focused)
        ShortcutClass = getattr(QtWidgets, 'QShortcut', None) or getattr(QtGui, 'QShortcut')
        for seq, fn in (
            (QtGui.QKeySequence(QtCore.Qt.CTRL | QtCore.Qt.Key_Left), lambda: self._nav_move(-1, 0)),
            (QtGui.QKeySequence(QtCore.Qt.CTRL | QtCore.Qt.Key_Right), lambda: self._nav_move(1, 0)),
            (QtGui.QKeySequence(QtCore.Qt.CTRL | QtCore.Qt.Key_Up), lambda: self._nav_move(0, -1)),
            (QtGui.QKeySequence(QtCore.Qt.CTRL | QtCore.Qt.Key_Down), lambda: self._nav_move(0, 1)),
        ):
            sc = ShortcutClass(seq, self)
            sc.setContext(QtCore.Qt.ApplicationShortcut)
            sc.activated.connect(fn)
        center_wrap = QtWidgets.QWidget()
        center_wrap.setLayout(center_box)
        hbox.addWidget(center_wrap, 1)

        # right: info + spinboxes
        right_box = QtWidgets.QVBoxLayout()
        right_box.addWidget(QtWidgets.QLabel('Інформація про гліф/ячейку'))
        self.info = QtWidgets.QTextEdit()
        self.info.setReadOnly(True)
        self.info.setFixedWidth(320)
        self.info.setFixedHeight(220)
        right_box.addWidget(self.info)

        form = QtWidgets.QGridLayout()
        lab_left = QtWidgets.QLabel('Left')
        lab_left.setToolTip('Лівий відступ (синя межа): кількість прозорих пікселів зліва до початку гліфа.')
        form.addWidget(lab_left, 0, 0)
        lab_glyph = QtWidgets.QLabel('Glyph')
        lab_glyph.setToolTip('Ширина гліфа (червона межа): видима ширина символу в пікселях від лівого краю комірки.')
        form.addWidget(lab_glyph, 1, 0)
        lab_char = QtWidgets.QLabel('Char')
        lab_char.setToolTip('Просування (червона межа): позиція правої “логічної” межі. За правилом: Char ≤ Glyph.')
        form.addWidget(lab_char, 2, 0)
        self.left_spin = QtWidgets.QSpinBox()
        self.left_spin.setToolTip('Лівий відступ (Left), px. Синя межа.')
        self.left_spin.setRange(-1024, 4096)
        self.left_spin.valueChanged.connect(self.on_width_changed)
        form.addWidget(self.left_spin, 0, 1)
        self.glyph_spin = QtWidgets.QSpinBox()
        self.glyph_spin.setToolTip('Ширина гліфа (Glyph), px. Червона межа.')
        self.glyph_spin.setRange(0, 4096)
        self.glyph_spin.valueChanged.connect(self.on_width_changed)
        form.addWidget(self.glyph_spin, 1, 1)
        self.char_spin = QtWidgets.QSpinBox()
        self.char_spin.setToolTip('Відстань просування (Char/Advance), px. Зелена межа. Не може бути більша за Glyph.')
        self.char_spin.setRange(0, 4096)
        self.char_spin.valueChanged.connect(self.on_width_changed)
        form.addWidget(self.char_spin, 2, 1)
        # Unicode mapping edit
        row0 = 3
        lab_unicode = QtWidgets.QLabel('Unicode код')
        lab_unicode.setToolTip('Код символу у форматі U+XXXX, 0xXX або десяткове. Авто‑синхронізується з полем “Символ”.')
        form.addWidget(lab_unicode, row0, 0)
        code_row = QtWidgets.QHBoxLayout()
        self.code_edit = QtWidgets.QLineEdit()
        self.code_edit.setPlaceholderText('U+0041 або 65 або 0x41')
        self.code_edit.setToolTip('Unicode-код символу: U+XXXX, 0xXX або десяткове.')
        self.code_edit.textChanged.connect(self.on_code_changed)
        self.code_preview = QtWidgets.QLabel('')
        self.code_preview.setToolTip('Превʼю символу для введеного коду.')
        self.code_preview.setMinimumWidth(24)
        code_row.addWidget(self.code_edit)
        code_row.addWidget(self.code_preview)
        code_row_w = QtWidgets.QWidget()
        code_row_w.setLayout(code_row)
        form.addWidget(code_row_w, row0, 1)

        lab_symbol = QtWidgets.QLabel('Символ')
        lab_symbol.setToolTip('Один символ. При зміні оновлює поле “Unicode код”.')
        form.addWidget(lab_symbol, row0 + 1, 0)
        self.char_edit = QtWidgets.QLineEdit()
        self.char_edit.setToolTip('Символ (1 знак). Змінює відповідний Unicode-код.')
        self.char_edit.setMaxLength(2)
        self.char_edit.textChanged.connect(self.on_char_changed)
        form.addWidget(self.char_edit, row0 + 1, 1)
        form_w = QtWidgets.QWidget()
        form_w.setLayout(form)
        right_box.addWidget(form_w)

        # Кнопка збереження не потрібна — зміни зберігаються автоматично при переході між комірками
        self.btn_auto = QtWidgets.QPushButton('Авто ширина із зображення')
        self.btn_auto.setToolTip('Автовизначення Left/Glyph/Char за пікселями гліфа у комірці.')
        self.btn_auto.clicked.connect(self.auto_set_widths_from_image)
        right_box.addWidget(self.btn_auto)
        auto_pad_row = QtWidgets.QHBoxLayout()
        lab_autopad = QtWidgets.QLabel('Auto pad')
        lab_autopad.setToolTip('Запас для Char після автопошуку ширини. З правилом Char ≤ Glyph: Char = max(0, Glyph − pad).')
        auto_pad_row.addWidget(lab_autopad)
        self.auto_pad_spin = QtWidgets.QSpinBox()
        self.auto_pad_spin.setToolTip('Додати цей запас до Char після автопідбору.')
        self.auto_pad_spin.setRange(0, 64)
        self.auto_pad_spin.setValue(0)
        self.auto_pad_spin.valueChanged.connect(lambda v: self.settings.setValue('auto_pad', int(v)))
        auto_pad_row.addWidget(self.auto_pad_spin)
        auto_pad_w = QtWidgets.QWidget()
        auto_pad_w.setLayout(auto_pad_row)
        right_box.addWidget(auto_pad_w)

        auto_thr_row = QtWidgets.QHBoxLayout()
        lab_autoth = QtWidgets.QLabel('Auto threshold')
        lab_autoth.setToolTip('Поріг 0..255 для виявлення “значущих” пікселів. У режимі Alpha only — поріг альфи, інакше — премультиплікована яскравість.')
        auto_thr_row.addWidget(lab_autoth)
        self.auto_thr_spin = QtWidgets.QSpinBox()
        self.auto_thr_spin.setToolTip('Поріг (0..255) для визначення значущих пікселів (альфа/яскравість).')
        self.auto_thr_spin.setRange(0, 255)
        self.auto_thr_spin.setValue(16)
        self.auto_thr_spin.valueChanged.connect(lambda v: self.settings.setValue('auto_thr', int(v)))
        auto_thr_row.addWidget(self.auto_thr_spin)
        # Adaptive threshold controls
        self.auto_adaptive_chk = QtWidgets.QCheckBox('Adaptive')
        self.auto_adaptive_chk.setToolTip('Адаптивний поріг: обчислюється за квантилем максимумів по стовпцях у комірці.')
        self.auto_adaptive_chk.toggled.connect(lambda v: self.settings.setValue('auto_adaptive', bool(v)))
        auto_thr_row.addWidget(self.auto_adaptive_chk)
        self.auto_quantile_spin = QtWidgets.QDoubleSpinBox()
        self.auto_quantile_spin.setToolTip('Квантиль (0.50..0.99). За цим квантилем обирається поріг для відсікання фону.')
        self.auto_quantile_spin.setDecimals(2)
        self.auto_quantile_spin.setRange(0.50, 0.99)
        self.auto_quantile_spin.setSingleStep(0.05)
        self.auto_quantile_spin.setValue(0.60)
        self.auto_quantile_spin.valueChanged.connect(lambda v: self.settings.setValue('auto_quantile', float(v)))
        auto_thr_row.addWidget(self.auto_quantile_spin)
        auto_thr_w = QtWidgets.QWidget()
        auto_thr_w.setLayout(auto_thr_row)
        right_box.addWidget(auto_thr_w)

        self.use_alpha_chk = QtWidgets.QCheckBox('Alpha only (if present)')
        self.use_alpha_chk.setToolTip('Використовувати лише альфу, якщо PNG має альфа-канал.')
        self.use_alpha_chk.setChecked(True)
        self.use_alpha_chk.toggled.connect(lambda v: self.settings.setValue('use_alpha', bool(v)))
        right_box.addWidget(self.use_alpha_chk)

        right_box.addStretch(1)
        right_wrap = QtWidgets.QWidget()
        right_wrap.setLayout(right_box)
        right_wrap.setFixedWidth(360)
        hbox.addWidget(right_wrap)

        # Status bar indicator for save state
        self.status = self.statusBar()
        self.status_dirty_label = QtWidgets.QLabel('Saved')
        self.status.addPermanentWidget(self.status_dirty_label)

        # scene items
        self.pixmap_item = QtWidgets.QGraphicsPixmapItem()
        self.scene.addItem(self.pixmap_item)
        self.grid_item = None  # set after meta
        # overlays
        self.sel_rect_item = QtWidgets.QGraphicsRectItem()
        pen_sel = QtGui.QPen(QtGui.QColor('#FFFF00'))
        pen_sel.setWidth(2)
        pen_sel.setCosmetic(True)
        self.sel_rect_item.setPen(pen_sel)
        self.sel_rect_item.setVisible(False)
        self.scene.addItem(self.sel_rect_item)

        self.left_area_item = QtWidgets.QGraphicsRectItem()
        self.left_area_item.setBrush(QtGui.QColor(0, 0, 255, 90))
        self.left_area_item.setPen(QtCore.Qt.NoPen)
        self.left_area_item.setVisible(False)
        self.scene.addItem(self.left_area_item)

        self.left_line_item = QtWidgets.QGraphicsLineItem()
        pen_left = QtGui.QPen(QtGui.QColor(0, 0, 255))
        pen_left.setWidth(2)
        pen_left.setCosmetic(True)
        self.left_line_item.setPen(pen_left)
        self.left_line_item.setVisible(False)
        self.scene.addItem(self.left_line_item)

        self.glyph_outline_item = QtWidgets.QGraphicsRectItem()
        pen_g = QtGui.QPen(QtGui.QColor('#FF0000'))
        pen_g.setWidth(2)
        pen_g.setCosmetic(True)
        self.glyph_outline_item.setPen(pen_g)
        self.glyph_outline_item.setVisible(False)
        self.scene.addItem(self.glyph_outline_item)

        self.char_outline_item = QtWidgets.QGraphicsRectItem()
        pen_c = QtGui.QPen(QtGui.QColor('#00FF00'))
        pen_c.setWidth(2)
        pen_c.setCosmetic(True)
        self.char_outline_item.setPen(pen_c)
        self.char_outline_item.setVisible(False)
        self.scene.addItem(self.char_outline_item)

        # restore settings and open last folder if available
        QtCore.QTimer.singleShot(100, self._restore_settings_and_boot)

    # ---- data/meta ----
    def choose_folder(self):
        folder = QtWidgets.QFileDialog.getExistingDirectory(self, 'Оберіть теку з font.json та sheet_*.png')
        if not folder:
            return
        if not os.path.isfile(os.path.join(folder, 'font.json')):
            QtWidgets.QMessageBox.critical(self, 'Помилка', 'У теці немає font.json')
            return
        self.folder = folder
        self.settings.setValue('last_folder', self.folder)
        self.load_meta()

    def load_meta(self):
        with open(os.path.join(self.folder, 'font.json'), 'r', encoding='utf-8') as f:
            self.meta = json.load(f)
        self.tglp = self.meta.get('tglp', {})
        self.glyphs = self.meta.get('glyphs', [])
        self.sheet_png = [p for p in self.meta.get('sheet_png', []) if p.endswith('.png')]
        self.cw = int(self.tglp.get('cell_width', 0))
        self.ch = int(self.tglp.get('cell_height', 0))
        self.rows = int(self.tglp.get('rows', 0))
        self.cols = int(self.tglp.get('cols', 0))
        self.real_w = self.cw + 1
        self.real_h = self.ch + 1
        self.per_sheet = self.rows * self.cols if self.rows and self.cols else 0

        # index map
        self.index_to_glyphs.clear()
        for g in self.glyphs:
            self.index_to_glyphs.setdefault(int(g.get('index', 0)), []).append(g)

        # list
        self.list_png.clear()
        for p in self.sheet_png:
            self.list_png.addItem(p)
        if self.sheet_png:
            self.list_png.setCurrentRow(0)
            self.on_select_png_row(0)

        # grid item
        if self.grid_item is not None:
            self.scene.removeItem(self.grid_item)
            self.grid_item = None
        if self.rows and self.cols and self.cw and self.ch:
            self.grid_item = GridItem(self.cw, self.ch, self.rows, self.cols)
            self.scene.addItem(self.grid_item)
            self.grid_item.stackBefore(self.sel_rect_item)  # keep grid below overlays
        self.update_scene_rect()

    def on_select_png_row(self, row: int):
        # автозбереження перед перемиканням аркуша
        self._autosave_current_if_dirty()
        if row < 0 or row >= len(self.sheet_png):
            return
        name = self.sheet_png[row]
        self.current_png = os.path.join(self.folder, name)
        try:
            base = os.path.basename(name)
            i = base.split('.')[0].split('_')[1]
            self.current_sheet_index = int(i)
        except Exception:
            self.current_sheet_index = 0
        self.load_image()

    # ---- scene/image ----
    def load_image(self):
        img = QtGui.QImage(self.current_png)
        if img.isNull():
            QtWidgets.QMessageBox.critical(self, 'Помилка', 'Не вдалось відкрити PNG')
            return
        # store original (may have alpha) and composite for display only
        self.orig_img = img
        self.orig_has_alpha = img.hasAlphaChannel()
        if self.orig_has_alpha:
            dest = QtGui.QImage(img.size(), QtGui.QImage.Format_RGB32)
            dest.fill(QtGui.QColor(0, 0, 0))
            painter = QtGui.QPainter(dest)
            painter.setCompositionMode(QtGui.QPainter.CompositionMode_SourceOver)
            painter.drawImage(0, 0, img)
            painter.end()
            img = dest
        self.pixmap_item.setPixmap(QtGui.QPixmap.fromImage(img))
        self.update_pixmap_transform()
        self.update_scene_rect()

    def update_pixmap_transform(self):
        t = QtGui.QTransform()
        if self.flip_y:
            t.scale(1, -1)
            # move down by image height to keep in positive coords
            pm = self.pixmap_item.pixmap()
            t.translate(0, -pm.height())
        for _ in range(self.rotate_q % 4):
            # rotate around origin (0,0); QGraphicsPixmapItem at (0,0)
            t.rotate(90)
        self.pixmap_item.setTransform(t)

    def update_scene_rect(self):
        # base grid rect
        grid_w = self.rows * self.real_w + 2
        grid_h = self.cols * self.real_h + 2
        rect = QtCore.QRectF(0, 0, grid_w, grid_h)
        # union with transformed pixmap
        if not self.pixmap_item.pixmap().isNull():
            mapped = self.pixmap_item.mapRectToScene(self.pixmap_item.boundingRect())
            rect = rect.united(mapped)
        # include overlays so negative left is scrollable
        for it in (self.sel_rect_item, self.left_area_item, self.left_line_item, self.glyph_outline_item, self.char_outline_item):
            if it is not None and it.isVisible():
                rect = rect.united(it.mapRectToScene(it.boundingRect()))
        self.scene.setSceneRect(rect)

    def on_scale_changed(self, val: float):
        self.view.set_scale(val)

    def on_view_scale_changed(self, val: float):
        self.scale_spin.blockSignals(True)
        self.scale_spin.setValue(float(val))
        self.scale_spin.blockSignals(False)
        self.settings.setValue('scale', float(val))

    def on_view_clicked(self, p: QtCore.QPointF):
        if self.rows <= 0 or self.cols <= 0:
            return
        gx = int((p.x() - 1) // self.real_w)
        gy = int((p.y() - 1) // self.real_h)
        if gx < 0 or gy < 0 or gx >= self.rows or gy >= self.cols:
            return
        # автозбереження перед переходом на іншу комірку
        self._autosave_current_if_dirty()
        self.selected_cell = (gx, gy)
        self.populate_info_panel(gx, gy)
        self.update_overlays()
        self._ensure_selected_visible()

    # ---- widths / overlay ----
    def cell_to_index(self, gx: int, gy: int) -> int:
        rem = gy * self.rows + gx
        return self.current_sheet_index * self.per_sheet + rem

    def get_width_for_index(self, idx: int):
        lst = self.index_to_glyphs.get(idx, [])
        if lst:
            return lst[0].get('width')
        return None

    def get_display_width_for_index(self, idx: int):
        if self.selected_cell is not None and idx == self.cell_to_index(*self.selected_cell):
            return {
                'left': int(self.left_spin.value()),
                'glyph': int(self.glyph_spin.value()),
                'char': int(self.char_spin.value()),
            }
        return self.get_width_for_index(idx)

    def populate_info_panel(self, gx: int, gy: int):
        idx = self.cell_to_index(gx, gy)
        items = self.index_to_glyphs.get(idx, [])
        lines = [f'Комірка: ({gx},{gy})', f'Гліф-індекс: {idx}', f'Аркуш: {self.current_sheet_index}', f'К-ть символів: {len(items)}']
        for it in items[:10]:
            lines.append(f" - {it.get('char','')} {it.get('codepoint','')}")
        self.info.setText('\n'.join(lines))
        w = self.get_width_for_index(idx) or {'left': 0, 'glyph': 0, 'char': 0}
        self.left_spin.blockSignals(True)
        self.glyph_spin.blockSignals(True)
        self.char_spin.blockSignals(True)
        self.left_spin.setValue(int(w.get('left', 0)))
        self.glyph_spin.setValue(int(w.get('glyph', 0)))
        self.char_spin.setValue(int(w.get('char', 0)))
        self.left_spin.blockSignals(False)
        self.glyph_spin.blockSignals(False)
        self.char_spin.blockSignals(False)

        # primary glyph mapping for this index (edit first one)
        code_s = ''
        char_s = ''
        if items:
            code_s = str(items[0].get('codepoint', ''))
            char_s = str(items[0].get('char', ''))
        self._block_code_char_signals(True)
        self.code_edit.setText(code_s)
        self.char_edit.setText(char_s)
        self._update_code_preview_from_code_text()
        self._block_code_char_signals(False)

    def update_overlays(self):
        if self.selected_cell is None:
            self.sel_rect_item.setVisible(False)
            self.left_area_item.setVisible(False)
            self.glyph_outline_item.setVisible(False)
            self.char_outline_item.setVisible(False)
            return
        gx, gy = self.selected_cell
        x0 = gx * self.real_w + 1
        y0 = gy * self.real_h + 1
        x1 = x0 + self.cw
        y1 = y0 + self.ch
        self.sel_rect_item.setRect(QtCore.QRectF(x0, y0, self.cw, self.ch))
        self.sel_rect_item.setVisible(True)

        idx = self.cell_to_index(gx, gy)
        w = self.get_display_width_for_index(idx) or {'left': 0, 'glyph': 0, 'char': 0}
        left = int(w.get('left', 0))
        glyphw = max(0, int(w.get('glyph', 0)))
        charw = max(0, int(w.get('char', 0)))

        lx1 = x0 + left
        gx1 = x0 + glyphw
        cx1 = x0 + charw

        # left area (semi-transparent blue)
        if left > 0:
            self.left_area_item.setRect(QtCore.QRectF(x0, y0, left, self.ch))
            self.left_area_item.setVisible(True)
        else:
            self.left_area_item.setVisible(False)
        # left guide line always visible
        self.left_line_item.setLine(lx1, y0, lx1, y1)
        self.left_line_item.setVisible(True)

        # glyph/char outlines
        self.glyph_outline_item.setRect(QtCore.QRectF(x0, y0, glyphw, self.ch))
        self.glyph_outline_item.setVisible(True)
        self.char_outline_item.setRect(QtCore.QRectF(x0, y0, charw, self.ch))
        self.char_outline_item.setVisible(True)
        self.update_scene_rect()

    def on_width_changed(self, val: int):
        # live preview only
        # enforce constraints: Glyph ≥ Left, Char ≤ Glyph
        l = int(self.left_spin.value())
        g = int(self.glyph_spin.value())
        c = int(self.char_spin.value())
        if g < l:
            g = l
            self.glyph_spin.blockSignals(True)
            self.glyph_spin.setValue(g)
            self.glyph_spin.blockSignals(False)
        if c > g:
            c = g
            self.char_spin.blockSignals(True)
            self.char_spin.setValue(c)
            self.char_spin.blockSignals(False)
        self.update_overlays()
        self._dirty = True

    # ---- drag handles on overlays ----
    def eventFilter(self, obj, event):
        # Global intercept: Ctrl+Arrows anywhere navigate cells
        if event.type() == QtCore.QEvent.KeyPress:
            try:
                mods = event.modifiers()
                key = event.key()
            except Exception:
                mods = 0
                key = None
            if mods & QtCore.Qt.ControlModifier and key in (
                QtCore.Qt.Key_Left, QtCore.Qt.Key_Right, QtCore.Qt.Key_Up, QtCore.Qt.Key_Down
            ):
                if self._handle_nav_key(key):
                    event.accept()
                    return True
        # Intercept mouse on viewport, keys on view, to avoid default scrolling
        if obj is self.view.viewport():
            t = event.type()
            if t == QtCore.QEvent.MouseButtonPress and event.button() == QtCore.Qt.LeftButton:
                pos = (event.position().toPoint() if PYSIDE else event.pos())
                if self._begin_drag_if_on_handle(pos):
                    return True
            elif t == QtCore.QEvent.MouseMove:
                if self._drag_active:
                    pos = (event.position().toPoint() if PYSIDE else event.pos())
                    self._update_drag(pos)
                    return True
            elif t == QtCore.QEvent.MouseButtonRelease and event.button() == QtCore.Qt.LeftButton:
                if self._drag_active:
                    self._end_drag()
                    return True
        elif obj is self.view:
            if event.type() == QtCore.QEvent.KeyPress:
                mods = event.modifiers()
                # Only handle Ctrl+Arrows here to avoid clashing with text inputs; PgUp/PgDn still handled
                if mods & QtCore.Qt.ControlModifier:
                    if self._handle_nav_key(event.key()):
                        event.accept()
                        return True
                else:
                    if event.key() in (QtCore.Qt.Key_PageUp, QtCore.Qt.Key_PageDown):
                        if self._handle_nav_key(event.key()):
                            event.accept()
                            return True
        return super().eventFilter(obj, event)

    def _handle_nav_key(self, key: int) -> bool:
        handled = False
        if self.rows > 0 and self.cols > 0:
            if self.selected_cell is None:
                self.selected_cell = (0, 0)
                self.populate_info_panel(0, 0)
                self.update_overlays()
                handled = True
            else:
                gx, gy = self.selected_cell
                if key in (QtCore.Qt.Key_Left, QtCore.Qt.Key_Right, QtCore.Qt.Key_Up, QtCore.Qt.Key_Down):
                    nx, ny = self._compute_wrap_move(gx, gy, key)
                    if (nx, ny) != (gx, gy):
                        gx, gy = nx, ny
                        handled = True
                if handled:
                    self._autosave_current_if_dirty()
                    self.selected_cell = (gx, gy)
                    self.populate_info_panel(gx, gy)
                    self.update_overlays()
                    self._ensure_selected_visible()
                    return True
        if key == QtCore.Qt.Key_PageUp:
            row = self.list_png.currentRow()
            if row > 0:
                self.list_png.setCurrentRow(row - 1)
                return True
        if key == QtCore.Qt.Key_PageDown:
            row = self.list_png.currentRow()
            if row < self.list_png.count() - 1:
                self.list_png.setCurrentRow(row + 1)
                return True
        return False

    def _compute_wrap_move(self, gx: int, gy: int, key: int):
        if key == QtCore.Qt.Key_Left:
            if gx > 0:
                return gx - 1, gy
            else:
                return self.rows - 1, (gy - 1) % self.cols
        if key == QtCore.Qt.Key_Right:
            if gx < self.rows - 1:
                return gx + 1, gy
            else:
                return 0, (gy + 1) % self.cols
        if key == QtCore.Qt.Key_Up:
            return gx, (gy - 1) % self.cols
        if key == QtCore.Qt.Key_Down:
            return gx, (gy + 1) % self.cols
        return gx, gy

    def _nav_move(self, dx: int, dy: int):
        if self.rows <= 0 or self.cols <= 0:
            return
        if self.selected_cell is None:
            self.selected_cell = (0, 0)
            self.populate_info_panel(0, 0)
            self.update_overlays()
            return
        gx, gy = self.selected_cell
        if dx < 0:
            gx, gy = self._compute_wrap_move(gx, gy, QtCore.Qt.Key_Left)
        elif dx > 0:
            gx, gy = self._compute_wrap_move(gx, gy, QtCore.Qt.Key_Right)
        if dy < 0:
            gx, gy = self._compute_wrap_move(gx, gy, QtCore.Qt.Key_Up)
        elif dy > 0:
            gx, gy = self._compute_wrap_move(gx, gy, QtCore.Qt.Key_Down)
        self._autosave_current_if_dirty()
        self.selected_cell = (gx, gy)
        self.populate_info_panel(gx, gy)
        self.update_overlays()
        self._ensure_selected_visible()

    _drag_active = False
    _drag_kind = None  # 'left' | 'glyph' | 'char'
    _drag_cell_origin = QtCore.QPointF()

    def _begin_drag_if_on_handle(self, pos_widget):
        if self.selected_cell is None:
            return False
        p = self.view.mapToScene(pos_widget)
        gx, gy = self.selected_cell
        x0 = gx * self.real_w + 1
        y0 = gy * self.real_h + 1
        # current values
        idx = self.cell_to_index(gx, gy)
        w = self.get_display_width_for_index(idx) or {'left': 0, 'glyph': 0, 'char': 0}
        left = int(w.get('left', 0))
        glyphw = int(w.get('glyph', 0))
        charw = int(w.get('char', 0))
        lx = x0 + left
        gxpos = x0 + glyphw
        cxpos = x0 + charw
        # pick nearest handle within threshold in scene units
        thresh = 4.0
        dx_left = abs(p.x() - lx)
        dx_g = abs(p.x() - gxpos)
        dx_c = abs(p.x() - cxpos)
        best = min(dx_left, dx_g, dx_c)
        if best > thresh:
            return False
        if best == dx_left:
            self._drag_kind = 'left'
        elif best == dx_g:
            self._drag_kind = 'glyph'
        else:
            self._drag_kind = 'char'
        self._drag_active = True
        self._drag_cell_origin = QtCore.QPointF(x0, y0)
        self.setCursor(QtCore.Qt.SizeHorCursor)
        return True

    def _update_drag(self, pos_widget):
        if not self._drag_active or self._drag_kind is None or self.selected_cell is None:
            return
        p = self.view.mapToScene(pos_widget)
        x0 = self._drag_cell_origin.x()
        dx = int(round(p.x() - x0))
        if self._drag_kind == 'left':
            # allow negative left to spin minimum; cap to cell right
            rel = max(self.left_spin.minimum(), min(self.cw, dx))
            self.left_spin.setValue(rel)
            if self.glyph_spin.value() < rel:
                self.glyph_spin.setValue(rel)
            # keep Char ≤ Glyph: if char is over glyph, pull it back
            if self.char_spin.value() > self.glyph_spin.value():
                self.char_spin.setValue(self.glyph_spin.value())
        elif self._drag_kind == 'glyph':
            rel = max(0, min(self.cw, dx))
            if rel < self.left_spin.value():
                rel = self.left_spin.value()
            self.glyph_spin.setValue(rel)
            # keep Char ≤ Glyph: if char > glyph, clamp down
            if self.char_spin.value() > rel:
                self.char_spin.setValue(rel)
        elif self._drag_kind == 'char':
            rel = max(0, min(self.cw, dx))
            # clamp char to ≤ glyph
            if rel > self.glyph_spin.value():
                rel = int(self.glyph_spin.value())
            self.char_spin.setValue(rel)
        self.update_overlays()

    def _end_drag(self):
        self._drag_active = False
        self._drag_kind = None
        self.unsetCursor()

    def _ensure_selected_visible(self, margin: int = 24):
        if self.selected_cell is None:
            return
        # Prefer ensuring the visible selection rectangle item
        try:
            if hasattr(self, 'sel_rect_item') and self.sel_rect_item is not None:
                self.view.ensureVisible(self.sel_rect_item, margin, margin)
                return
        except Exception:
            pass
        # Fallback to computing the cell rect in scene coordinates
        try:
            gx, gy = self.selected_cell
            x0 = gx * self.real_w + 1
            y0 = gy * self.real_h + 1
            rect = QtCore.QRectF(x0, y0, self.cw, self.ch)
            self.view.ensureVisible(rect, margin, margin)
        except Exception:
            pass

    # ---- auto widths ----
    def auto_set_widths_from_image(self):
        if self.selected_cell is None or self.orig_img is None:
            return
        gx, gy = self.selected_cell
        x0 = gx * self.real_w + 1
        y0 = gy * self.real_h + 1
        img = self.orig_img
        use_alpha = self.use_alpha_chk.isChecked() and self.orig_has_alpha
        # Detect non-background pixels (alpha or brightness vs black)
        w = int(self.cw)
        h = int(self.ch)
        # build per-column maximum effective value for adaptive threshold if enabled
        fixed_thresh = int(self.auto_thr_spin.value())
        adaptive = getattr(self, 'auto_adaptive_chk', None) and self.auto_adaptive_chk.isChecked()
        col_max = [0] * w
        if adaptive:
            for cx in range(w):
                m = 0
                for cy in range(h):
                    sx = x0 + cx
                    sy = y0 + cy
                    if sx < 0 or sy < 0 or sx >= img.width() or sy >= img.height():
                        continue
                    col = img.pixelColor(int(sx), int(sy)) if hasattr(img, 'pixelColor') else QtGui.QColor(img.pixel(int(sx), int(sy)))
                    if use_alpha:
                        eff = col.alpha()
                    else:
                        a = col.alpha()
                        lum = (col.red()*3 + col.green()*6 + col.blue()*1) // 10
                        eff = (lum * a) // 255 if img.hasAlphaChannel() else lum
                    if eff > m:
                        m = eff
                col_max[cx] = m
            nonzero = sorted([v for v in col_max if v > 0])
            if nonzero:
                q = float(getattr(self, 'auto_quantile_spin', None).value() if hasattr(self, 'auto_quantile_spin') else 0.60)
                idx = max(0, min(len(nonzero) - 1, int(round(q * (len(nonzero) - 1)))))
                thresh = nonzero[idx]
            else:
                thresh = fixed_thresh
        else:
            thresh = fixed_thresh
        left_col = None
        right_col = None
        for cx in range(w):
            for cy in range(h):
                sx = x0 + cx
                sy = y0 + cy
                if sx < 0 or sy < 0 or sx >= img.width() or sy >= img.height():
                    continue
                col = img.pixelColor(int(sx), int(sy)) if hasattr(img, 'pixelColor') else QtGui.QColor(img.pixel(int(sx), int(sy)))
                if use_alpha:
                    if col.alpha() > thresh:
                        left_col = cx
                        break
                else:
                    # If image has alpha, many PNGs keep white RGB under transparent pixels.
                    # Use premultiplied luminance to ignore transparent background.
                    a = col.alpha()
                    lum = (col.red()*3 + col.green()*6 + col.blue()*1) // 10
                    eff = (lum * a) // 255 if img.hasAlphaChannel() else lum
                    if eff > thresh:
                        left_col = cx
                        break
            if left_col is not None:
                break
        if left_col is None:
            # empty cell: set zeros
            self.left_spin.setValue(0)
            self.glyph_spin.setValue(0)
            self.char_spin.setValue(0)
            self.update_overlays()
            return
        for cx in range(w - 1, -1, -1):
            for cy in range(h):
                sx = x0 + cx
                sy = y0 + cy
                if sx < 0 or sy < 0 or sx >= img.width() or sy >= img.height():
                    continue
                col = img.pixelColor(int(sx), int(sy)) if hasattr(img, 'pixelColor') else QtGui.QColor(img.pixel(int(sx), int(sy)))
                if use_alpha:
                    if col.alpha() > thresh:
                        right_col = cx
                        break
                else:
                    a = col.alpha()
                    lum = (col.red()*3 + col.green()*6 + col.blue()*1) // 10
                    eff = (lum * a) // 255 if img.hasAlphaChannel() else lum
                    if eff > thresh:
                        right_col = cx
                        break
            if right_col is not None:
                break
        if right_col is None:
            right_col = left_col
        left = int(left_col)
        glyph = int(right_col + 1)
        if glyph < left:
            glyph = left
        pad = int(self.auto_pad_spin.value())
        # With rule Char ≤ Glyph, apply pad as reduction
        charw = max(0, glyph - pad)
        # apply and update
        self.left_spin.setValue(left)
        if self.glyph_spin.value() < left:
            self.glyph_spin.setValue(left)
        self.glyph_spin.setValue(glyph)
        self.char_spin.setValue(charw)
        self.update_overlays()

    def save_widths(self):
        if self.selected_cell is None:
            QtWidgets.QMessageBox.warning(self, 'Увага', 'Не вибрано комірку')
            return
        idx = self.cell_to_index(*self.selected_cell)
        updated = 0
        left = int(self.left_spin.value())
        glyphw = int(self.glyph_spin.value())
        charw = int(self.char_spin.value())
        for g in self.glyphs:
            if int(g.get('index', -1)) == idx:
                if not isinstance(g.get('width'), dict):
                    g['width'] = {}
                g['width']['left'] = left
                g['width']['glyph'] = glyphw
                g['width']['char'] = charw
                updated += 1
        # Save primary codepoint/char mapping on the first glyph with this index
        code_s, char_s = self._normalized_code_char_from_fields()
        for g in self.glyphs:
            if int(g.get('index', -1)) == idx:
                g['codepoint'] = code_s
                g['char'] = char_s
                break
        if updated:
            try:
                path = os.path.join(self.folder, 'font.json')
                with open(path, 'w', encoding='utf-8') as f:
                    json.dump(self.meta, f, ensure_ascii=False, indent=2)
                QtWidgets.QMessageBox.information(self, 'OK', f'Збережено {updated} запис(ів) ширин у font.json')
            except Exception as ex:
                QtWidgets.QMessageBox.critical(self, 'Помилка', f'Не вдалося зберегти font.json: {ex}')
        else:
            QtWidgets.QMessageBox.information(self, 'Інфо', 'Для цього індексу не знайдено гліфів')

    # ---- code/char edit helpers ----
    def _block_code_char_signals(self, block: bool):
        self.code_edit.blockSignals(block)
        self.char_edit.blockSignals(block)

    def _update_code_preview_from_code_text(self):
        cp = self._parse_code_text(self.code_edit.text())
        if cp is None:
            self.code_preview.setText('')
            return
        try:
            self.code_preview.setText(chr(cp))
        except Exception:
            self.code_preview.setText('')

    def _parse_code_text(self, s: str):
        s = (s or '').strip().upper()
        if not s:
            return None
        try:
            if s.startswith('U+'):
                return int(s[2:], 16)
            if s.startswith('0X'):
                return int(s, 16)
            if all(c in '0123456789ABCDEF' for c in s) and len(s) <= 6:
                return int(s, 16)
            # decimal fallback
            return int(s, 10)
        except Exception:
            return None

    def _format_code_u(self, cp: int) -> str:
        if cp < 0:
            cp = 0
        if cp > 0x10FFFF:
            cp = 0x10FFFF
        return f"U+{cp:04X}"

    def _normalized_code_char_from_fields(self):
        # Get consistent (codepoint string, char) from current editors
        cp = self._parse_code_text(self.code_edit.text())
        ch_text = self.char_edit.text() or ''
        # Prefer char if valid single codepoint
        if ch_text:
            c0 = ch_text[0]
            return self._format_code_u(ord(c0)), c0
        if cp is not None:
            try:
                return self._format_code_u(cp), chr(cp)
            except Exception:
                pass
        return 'U+0000', '\\u0000'

    # ---- autosave helpers ----
    def _autosave_current_if_dirty(self):
        if not getattr(self, '_dirty', False) or self.selected_cell is None:
            return
        try:
            idx = self.cell_to_index(*self.selected_cell)
            left = int(self.left_spin.value())
            glyphw = int(self.glyph_spin.value())
            charw = int(self.char_spin.value())
            updated = 0
            for g in self.glyphs:
                if int(g.get('index', -1)) == idx:
                    if not isinstance(g.get('width'), dict):
                        g['width'] = {}
                    g['width']['left'] = left
                    g['width']['glyph'] = glyphw
                    g['width']['char'] = charw
                    updated += 1
            code_s, char_s = self._normalized_code_char_from_fields()
            for g in self.glyphs:
                if int(g.get('index', -1)) == idx:
                    g['codepoint'] = code_s
                    g['char'] = char_s
                    break
            if updated:
                path = os.path.join(self.folder, 'font.json')
                with open(path, 'w', encoding='utf-8') as f:
                    json.dump(self.meta, f, ensure_ascii=False, indent=2)
                if hasattr(self, 'status_dirty_label'):
                    self.status_dirty_label.setText('Saved')
        except Exception:
            pass
        finally:
            self._dirty = False

    def closeEvent(self, event):
        try:
            self._autosave_current_if_dirty()
        except Exception:
            pass
        super().closeEvent(event)

    def _set_dirty(self, flag: bool):
        self._dirty = bool(flag)
        if hasattr(self, 'status_dirty_label'):
            self.status_dirty_label.setText('Unsaved' if self._dirty else 'Saved')

    # ---- keyboard navigation ----
    def keyPressEvent(self, event: QtGui.QKeyEvent):
        key = event.key()
        handled = False
        if self.rows > 0 and self.cols > 0:
            if self.selected_cell is None:
                self.selected_cell = (0, 0)
                self.populate_info_panel(0, 0)
                self.update_overlays()
                handled = True
            else:
                gx, gy = self.selected_cell
                if key == QtCore.Qt.Key_Left and gx > 0:
                    gx -= 1; handled = True
                elif key == QtCore.Qt.Key_Right and gx < self.rows - 1:
                    gx += 1; handled = True
                elif key == QtCore.Qt.Key_Up and gy > 0:
                    gy -= 1; handled = True
                elif key == QtCore.Qt.Key_Down and gy < self.cols - 1:
                    gy += 1; handled = True
                if handled:
                    self._autosave_current_if_dirty()
                    self.selected_cell = (gx, gy)
                    self.populate_info_panel(gx, gy)
                    self.update_overlays()
        if key == QtCore.Qt.Key_PageUp:
            row = self.list_png.currentRow()
            if row > 0:
                self.list_png.setCurrentRow(row - 1)
                handled = True
        elif key == QtCore.Qt.Key_PageDown:
            row = self.list_png.currentRow()
            if row < self.list_png.count() - 1:
                self.list_png.setCurrentRow(row + 1)
                handled = True
        if not handled:
            super().keyPressEvent(event)

    def on_code_changed(self, text: str):
        cp = self._parse_code_text(text)
        ch = ''
        if cp is not None:
            try:
                ch = chr(cp)
            except Exception:
                ch = ''
        self._block_code_char_signals(True)
        self.char_edit.setText(ch)
        # normalize display of code to U+XXXX when it looks valid
        if cp is not None:
            self.code_edit.setText(self._format_code_u(cp))
        self._block_code_char_signals(False)
        self._update_code_preview_from_code_text()
        self._set_dirty(True)

    def on_char_changed(self, text: str):
        ch = (text or '')
        if len(ch) > 0:
            c0 = ch[0]
            cp = ord(c0)
            self._block_code_char_signals(True)
            self.code_edit.setText(self._format_code_u(cp))
            self._block_code_char_signals(False)
        else:
            self._block_code_char_signals(True)
            self.code_edit.setText('')
            self._block_code_char_signals(False)
        self._update_code_preview_from_code_text()
        self._set_dirty(True)

    # ---- view transforms ----
    def toggle_flip_y(self):
        self.flip_y = not self.flip_y
        self.update_pixmap_transform()
        self.update_scene_rect()
        self.settings.setValue('flip_y', bool(self.flip_y))

    def rotate_90(self):
        self.rotate_q = (self.rotate_q + 1) % 4
        self.update_pixmap_transform()
        self.update_scene_rect()
        self.settings.setValue('rotate_q', int(self.rotate_q))

    # ---- settings restore ----
    def _restore_settings_and_boot(self):
        try:
            last_folder = self.settings.value('last_folder', '', type=str) or ''
        except Exception:
            last_folder = ''
        flip = self._get_setting_bool('flip_y', False)
        rot = int(self.settings.value('rotate_q', 0))
        scale = float(self.settings.value('scale', 1.0))
        auto_pad = int(self.settings.value('auto_pad', 0))
        auto_thr = int(self.settings.value('auto_thr', 16))
        use_alpha = self._get_setting_bool('use_alpha', True)
        auto_adaptive = self._get_setting_bool('auto_adaptive', False)
        auto_quantile = float(self.settings.value('auto_quantile', 0.60))

        self.flip_y = flip
        self.rotate_q = rot % 4
        self.view.set_scale(scale)
        self.scale_spin.setValue(scale)
        self.auto_pad_spin.setValue(auto_pad)
        self.auto_thr_spin.setValue(auto_thr)
        self.use_alpha_chk.setChecked(use_alpha)
        if hasattr(self, 'auto_adaptive_chk'):
            self.auto_adaptive_chk.setChecked(auto_adaptive)
        if hasattr(self, 'auto_quantile_spin'):
            try:
                self.auto_quantile_spin.setValue(float(auto_quantile))
            except Exception:
                pass

        if last_folder and os.path.isfile(os.path.join(last_folder, 'font.json')):
            self.folder = last_folder
            self.load_meta()
        else:
            self.choose_folder()

    def _get_setting_bool(self, key: str, default: bool) -> bool:
        try:
            v = self.settings.value(key, default)
            if isinstance(v, bool):
                return v
            if isinstance(v, (int, float)):
                return bool(v)
            s = str(v).strip().lower()
            return s in ('1', 'true', 't', 'yes', 'y', 'on')
        except Exception:
            return bool(default)


def main():
    app = QtWidgets.QApplication(sys.argv)
    w = BffntQtViewer()
    w.show()
    sys.exit(app.exec())


if __name__ == '__main__':
    main()
