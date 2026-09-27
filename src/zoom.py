"""Zoom da interface Tk (Ctrl + / Ctrl - / Ctrl 0).

Escala fontes (nomeadas e explícitas), medidas fixas em pixels, estilos ttk e o
DPI dos gráficos do matplotlib. O tamanho original de cada widget é guardado na
primeira passagem, então zoom repetido não acumula erro de arredondamento.
Diálogos (Toplevel) não são reescalados depois de abertos.
"""
import tkinter as tk
import tkinter.font as tkfont

BASE_SIZE = (1440, 900)
MIN_SIZE = (1080, 720)
DEFAULT_ZOOM = 1.2
ZOOM_STEP = 0.1
ZOOM_MIN = 0.7
ZOOM_MAX = 2.0
SCREEN_MARGIN = 80

NAMED_FONTS = ("TkDefaultFont", "TkTextFont", "TkFixedFont", "TkMenuFont", "TkHeadingFont",
               "TkCaptionFont", "TkSmallCaptionFont", "TkIconFont", "TkTooltipFont")
# Medidas em pixels por classe de widget; width/height só entram onde não são caracteres.
PIXEL_OPTIONS = {"Frame": ("width", "height", "padx", "pady"), "Label": ("padx", "pady", "wraplength"),
                 "Button": ("padx", "pady", "wraplength")}
FIGURE_DPI = 100


def scaled(value, factor):
    """Multiplica um tamanho preservando o sinal (fontes negativas são em pixels)."""
    return max(1, round(value * factor)) if value > 0 else min(-1, round(value * factor))


class ZoomControls:
    def init_zoom(self):
        """Chamar antes de criar os widgets: ajusta fontes nomeadas e janela."""
        width, height = self.root.winfo_screenwidth() - SCREEN_MARGIN, self.root.winfo_screenheight() - SCREEN_MARGIN
        fits = int(min(ZOOM_MAX, width / MIN_SIZE[0], height / MIN_SIZE[1]) * 10) / 10
        self.zoom_max = max(1.0, fits)
        self.zoom = min(DEFAULT_ZOOM, self.zoom_max)
        self._zoom_applied = 1.0
        self._zoom_fonts, self._zoom_dims = {}, {}
        self._zoom_named = {name: tkfont.nametofont(name).cget("size") for name in NAMED_FONTS}
        for sequence in ("<Control-plus>", "<Control-equal>", "<Control-KP_Add>"):
            self.root.bind_all(sequence, lambda event: self.zoom_by(ZOOM_STEP))
        for sequence in ("<Control-minus>", "<Control-KP_Subtract>"):
            self.root.bind_all(sequence, lambda event: self.zoom_by(-ZOOM_STEP))
        self.root.bind_all("<Control-0>", lambda event: self.set_zoom(DEFAULT_ZOOM))
        self._apply_named_fonts()
        self._fit_window(None)

    def zoom_by(self, delta):
        return self.set_zoom(self.zoom + delta)

    def set_zoom(self, value):
        value = round(min(self.zoom_max, max(ZOOM_MIN, value)), 2)
        if value != self.zoom:
            previous, self.zoom = self.zoom, value
            self._apply_named_fonts()
            self.apply_zoom()
            self._fit_window(previous)
        return "break"

    def _apply_named_fonts(self):
        for name, size in self._zoom_named.items():
            tkfont.nametofont(name).configure(size=scaled(size, self.zoom))
        self.root.option_add("*TCombobox*Listbox.font", "TkDefaultFont")

    def _fit_window(self, previous):
        """Ajusta tamanho mínimo e da janela; `previous` é o zoom anterior (None na abertura)."""
        limit = (self.root.winfo_screenwidth() - SCREEN_MARGIN, self.root.winfo_screenheight() - SCREEN_MARGIN)
        self.root.minsize(*(min(round(size * self.zoom), cap) for size, cap in zip(MIN_SIZE, limit)))
        if previous is None:
            size = [base * self.zoom for base in BASE_SIZE]
        else:
            size = [current * self.zoom / previous for current in (self.root.winfo_width(), self.root.winfo_height())]
        self.root.geometry("x".join(str(min(round(value), cap)) for value, cap in zip(size, limit)))

    def apply_zoom(self):
        """Reaplica o zoom a todos os widgets da janela principal."""
        self._configure_styles()
        self._zoom_widget(self.root)
        for figure, canvas in self._zoom_figures():
            figure.set_dpi(FIGURE_DPI * self.zoom)
            widget = canvas.get_tk_widget()
            if widget.winfo_width() > 1 and widget.winfo_height() > 1:
                figure.set_size_inches(widget.winfo_width() / figure.dpi, widget.winfo_height() / figure.dpi, forward=False)
            canvas.draw_idle()
        self._zoom_applied = self.zoom

    def _zoom_figures(self):
        pairs = (("fig", "canvas"), ("antenna_fig", "antenna_canvas"))
        return [(getattr(self, fig), getattr(self, canvas)) for fig, canvas in pairs if hasattr(self, canvas)]

    def _zoom_widget(self, widget):
        path = str(widget)
        if path not in self._zoom_fonts:
            self._zoom_fonts[path] = self._font_base(widget)
        if self._zoom_fonts[path]:
            family, size, styles = self._zoom_fonts[path]
            widget.configure(font=(family, scaled(size, self.zoom), *styles))
        if path not in self._zoom_dims:
            self._zoom_dims[path] = self._dimension_base(widget)
        for option, size in self._zoom_dims[path].items():
            widget.configure(**{option: scaled(size, self.zoom)})
        for child in widget.winfo_children():
            if not isinstance(child, tk.Toplevel):
                self._zoom_widget(child)

    @staticmethod
    def _dimension_base(widget):
        sizes = {option: str(widget.cget(option)) for option in PIXEL_OPTIONS.get(widget.winfo_class(), ())}
        return {option: int(size) for option, size in sizes.items() if size.isdigit() and int(size) > 0}

    def _font_base(self, widget):
        """(família, tamanho, estilos) originais de uma fonte explícita; None para fontes nomeadas."""
        try:
            value = str(widget.cget("font"))
        except tk.TclError:
            return None
        parts = self.root.tk.splitlist(value)
        if len(parts) < 2 or value in tkfont.names(self.root):
            return None
        try:
            return parts[0], round(int(parts[1]) / self._zoom_applied), tuple(parts[2:])
        except ValueError:
            return None

    def register_zoom_font(self, widget, family, size, styles):
        """Widgets criados já com zoom informam o tamanho base, para a próxima passagem."""
        self._zoom_fonts[str(widget)] = (family, size, tuple(styles))
