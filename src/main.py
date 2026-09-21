import dearpygui.dearpygui as dpg

INITIAL_WINDOW_WIDTH  = 1100
INITIAL_WINDOW_HEIGHT = 700

MINIMUM_WINDOW_WIDTH  = 920
MINIMUM_WINDOW_HEIGHT = 640


TOOLBAR_HEIGHT = 40
INPUT_HEIGHT   = 30
BUTTON_HEIGHT  = 30

RIGHT_PANEL_WIDTH = 220

SPACING = 8
PADDING = 8

NORMAL_TEXT = 14
STATUS_TEXT = 16

THEME = "neutral_engineering"

_last_width  = -1
_last_height = -1


THEMES = {
    "industrial_blue_grey": {
        "background": [30, 35, 40, 255],
        "panel": [40, 46, 52, 255],
        "input": [52, 59, 66, 255],
        "border": [80, 88, 96, 255],
        "text": [226, 230, 233, 255],
        "text_secondary": [168, 176, 183, 255],
        "accent": [76, 143, 203, 255],
    },

    "laboratory_slate": {
        "background": [37, 41, 45, 255],
        "panel": [48, 53, 58, 255],
        "input": [58, 64, 70, 255],
        "border": [85, 92, 98, 255],
        "text": [229, 231, 233, 255],
        "text_secondary": [173, 179, 184, 255],
        "accent": [107, 157, 184, 255],
    },

    "navy_instrument": {
        "background": [24, 35, 45, 255],
        "panel": [34, 49, 61, 255],
        "input": [45, 60, 72, 255],
        "border": [72, 89, 101, 255],
        "text": [224, 229, 232, 255],
        "text_secondary": [167, 178, 186, 255],
        "accent": [102, 152, 184, 255],
    },

    "light_industrial": {
        "background": [217, 220, 221, 255],
        "panel": [232, 233, 233, 255],
        "input": [244, 244, 243, 255],
        "border": [167, 170, 172, 255],
        "text": [37, 40, 42, 255],
        "text_secondary": [93, 98, 101, 255],
        "accent": [63, 111, 145, 255],
    },

    "classic_scada": {
        "background": [32, 36, 38, 255],
        "panel": [41, 45, 47, 255],
        "input": [53, 58, 61, 255],
        "border": [85, 90, 93, 255],
        "text": [216, 220, 221, 255],
        "text_secondary": [150, 156, 159, 255],
        "accent": [113, 143, 163, 255],
    },

    "neutral_engineering": {
        "background": [32, 32, 32, 255],
        "panel": [41, 41, 41, 255],
        "input": [53, 53, 53, 255],
        "border": [80, 80, 80, 255],
        "text": [224, 224, 224, 255],
        "text_secondary": [160, 160, 160, 255],
        "accent": [125, 154, 175, 255],
    },
}


def update_layout():
    global _last_width
    global _last_height

    width = dpg.get_viewport_client_width()
    height = dpg.get_viewport_client_height()

    if width <= 0 or height <= 0:
        return

    if width == _last_width and height == _last_height:
        return

    _last_width  = width
    _last_height = height

    graph_width  = width - 2 * PADDING - RIGHT_PANEL_WIDTH - SPACING
    graph_height = height - 2 * PADDING - TOOLBAR_HEIGHT - SPACING

    dpg.configure_item(
        "graph",
        width=graph_width,
        height=graph_height,
    )

    # dpg.configure_item(
    #     "toolbar",
    #     width=width - 2 * PADDING,
    # )

    print(dpg.get_item_height("toolbar"))
    print(dpg.get_item_height("status_panel"))
    print(dpg.get_item_height("control_panel"))

    right_panel_half_height = graph_height // 2 - PADDING / 2
    # print(f"Height: {height}")
    # print((f"Right panel: {right_panel_half_height}"))
    # print(2 * right_panel_half_height + PADDING * 6 + SPACING + TOOLBAR_HEIGHT - 23)
    # dpg.configure_item(
    #     "right_panel",
    #     height=graph_height,
    # )

    dpg.configure_item(
        "control_panel",
        height=right_panel_half_height,
    )

    dpg.configure_item(
        "status_panel",
        # height=graph_height - right_panel_half_height,
        height=right_panel_half_height,
    )


def on_viewport_resize():
    update_layout()


if __name__ == "__main__":
    dpg.create_context()

    colours = THEMES[THEME]

    with dpg.theme() as theme:
        with dpg.theme_component(dpg.mvAll):

            dpg.add_theme_color(
                dpg.mvThemeCol_WindowBg,
                colours["background"],
            )

            dpg.add_theme_color(
                dpg.mvThemeCol_ChildBg,
                colours["panel"],
            )

            dpg.add_theme_color(
                dpg.mvThemeCol_FrameBg,
                colours["input"],
            )

            dpg.add_theme_color(
                dpg.mvThemeCol_FrameBgHovered,
                colours["input"],
            )

            dpg.add_theme_color(
                dpg.mvThemeCol_FrameBgActive,
                colours["input"],
            )

            dpg.add_theme_color(
                dpg.mvThemeCol_Border,
                colours["border"],
            )

            dpg.add_theme_color(
                dpg.mvThemeCol_Text,
                colours["text"],
            )

            dpg.add_theme_color(
                dpg.mvThemeCol_TextDisabled,
                colours["text_secondary"],
            )

            dpg.add_theme_color(
                dpg.mvThemeCol_Button,
                colours["panel"],
            )

            dpg.add_theme_color(
                dpg.mvThemeCol_ButtonHovered,
                colours["accent"],
            )

            dpg.add_theme_color(
                dpg.mvThemeCol_ButtonActive,
                colours["accent"],
            )

            dpg.add_theme_color(
                dpg.mvThemeCol_Header,
                colours["accent"],
            )

            dpg.add_theme_color(
                dpg.mvThemeCol_HeaderHovered,
                colours["accent"],
            )

            dpg.add_theme_color(
                dpg.mvThemeCol_HeaderActive,
                colours["accent"],
            )

            dpg.add_theme_color(
                dpg.mvThemeCol_CheckMark,
                colours["accent"],
            )

            dpg.add_theme_color(
                dpg.mvThemeCol_SliderGrab,
                colours["accent"],
            )

            dpg.add_theme_color(
                dpg.mvThemeCol_SliderGrabActive,
                colours["accent"],
            )

            dpg.add_theme_style(
                dpg.mvStyleVar_WindowPadding,
                PADDING,
                PADDING,
            )

            dpg.add_theme_style(
                dpg.mvStyleVar_ItemSpacing,
                SPACING,
                SPACING,
            )

            dpg.add_theme_style(
                dpg.mvStyleVar_FramePadding,
                PADDING,
                PADDING,
            )

            dpg.add_theme_style(
                dpg.mvStyleVar_WindowBorderSize,
                0,
            )

            dpg.add_theme_style(dpg.mvStyleVar_ScrollbarSize,
                                0,
                                0,)

        dpg.bind_theme(theme)

    with dpg.window(
        tag="root",
        width=INITIAL_WINDOW_WIDTH,
        height=INITIAL_WINDOW_HEIGHT,
        no_scroll_with_mouse=True,
        no_scrollbar=True,
    ):
        
        with dpg.child_window(
            tag="toolbar",
            # border=False,
            no_scroll_with_mouse=True,
            no_scrollbar=True,
            height = TOOLBAR_HEIGHT,
        ):
            dpg.add_text("toolbar")

        with dpg.group(tag="main_group", horizontal=True):
            with dpg.child_window(
                tag="graph", border=True,
                no_scroll_with_mouse=True,
                no_scrollbar=True,
                ):
                dpg.add_text("graph")

            with dpg.group(tag="right_panel_group"):
                with dpg.child_window(
                    tag="control_panel",
                    border=True,
                    no_scroll_with_mouse=True,
                    no_scrollbar=True,
                    width=RIGHT_PANEL_WIDTH,
                ):
                    dpg.add_text("Controls")
                    
                with dpg.child_window(
                    tag="status_panel",
                    border=True,
                    no_scroll_with_mouse=True,
                    no_scrollbar=True,
                    width=RIGHT_PANEL_WIDTH,
                ):
                    dpg.add_text("Status")

    dpg.create_viewport(
        title="Placeholder Name",
        width=INITIAL_WINDOW_WIDTH,
        height=INITIAL_WINDOW_HEIGHT,
        min_width=MINIMUM_WINDOW_WIDTH,
        min_height=MINIMUM_WINDOW_HEIGHT,
        resizable=True,
    )

    dpg.set_viewport_resize_callback(on_viewport_resize)

    dpg.setup_dearpygui()
    dpg.show_viewport()
    dpg.set_primary_window("root", True)

    dpg.set_frame_callback(1, update_layout)

    while dpg.is_dearpygui_running():
        update_layout()
        dpg.render_dearpygui_frame()

    dpg.destroy_context()