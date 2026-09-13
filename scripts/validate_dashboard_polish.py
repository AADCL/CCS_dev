"""Run only the v0.24.1 dashboard increment in isolated child processes.

Use --dpi 1.25 (also 1.5 / 2) for the focused main-window geometry check.
All other arguments are forwarded to the existing isolated runner.
"""
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts import validate_dashboard_layout as runner

BASE_JOBS = (
    'tests.test_dashboard_layout',
    'tests.test_command_dashboard.CommandDashboardUiTests.test_all_online_device_types_are_listed_and_task_controls_are_disabled',
    'tests.test_command_dashboard.CommandDashboardUiTests.test_page_timers_scan_removal_and_manual_fullscreen_lifecycle',
    'tests.test_command_dashboard.CommandDashboardUiTests.test_status_panel_responds_to_width_without_overriding_user_choice',
    'tests.test_command_dashboard.CommandDashboardUiTests.test_chart_header_device_panel_and_console_collapsing',
    'tests.test_command_dashboard.CommandDashboardUiTests.test_selecting_device_seeds_latest_telemetry_and_hides_cursor_coordinates',
    'tests.test_app_icons.AppIconTests.test_required_theme_icons_exist_and_load',
    'tests.test_app_icons.AppIconTests.test_icon_resolution_does_not_depend_on_working_directory',
    'tests.test_app_icons.AppIconTests.test_application_assets_have_exact_names_single_svg_and_render',
    'tests.test_theme_v010.DayThemeTests.test_all_base_style_colors_are_translated_for_day_theme',
    'tests.test_theme_v010.DayThemeTests.test_selected_device_card_uses_day_palette',
    'tests.test_ui.UiTests.test_theme_button_switches_theme_without_changing_page',
    'tests.test_version',
    'tests.test_release_engineering.RuntimePathsTests.test_frozen_data_and_resources_are_separate',
    'tests.test_release_engineering.ReleaseContentsTests.test_portable_uses_clean_defaults_and_contains_no_live_data',
    'tests.test_task_system.TaskSystemTests.test_conflict_detector_respects_time_altitude_and_delay',
    'tests.test_task_map',
    'tests.test_task_page_ui',
)


def main():
    runner.JOBS = (*BASE_JOBS, 'tests.test_dashboard_polish')
    output = 'build/polish-validation'
    if '--dpi' in sys.argv:
        index = sys.argv.index('--dpi')
        scale = sys.argv[index + 1]
        if scale not in {'1', '1.25', '1.5', '2'}:
            raise SystemExit('Supported DPI scales: 1, 1.25, 1.5, 2')
        del sys.argv[index:index + 2]
        os.environ['QT_SCALE_FACTOR'] = scale
        runner.JOBS = ('tests.test_dashboard_polish.DashboardPolishTests.test_main_window_labels_icons_and_long_device_at_all_sizes',)
        output = f'build/polish-dpi-{scale}'
    if '--output' not in sys.argv:
        sys.argv.extend(('--output', output))
    return runner.main()


if __name__ == '__main__':
    raise SystemExit(main())
