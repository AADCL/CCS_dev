"""Internal release smoke fixture, executed in a disposable frozen worker."""
def fuse_maps(pcd_files, primary_frame, transforms, output_pcd, options):
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication
    import ccs_monitor.app as application
    from ccs_monitor.map_fusion import voxel_merge
    # Network behavior is tested separately against localhost. Do not bind field ports.
    for cls in (application.NtpServerService, application.MqttMonitoringRuntime,
                application.UdpMonitoringRuntime, application.MapBuildingService,
                application.RelocalizationService, application.TaskExecutionService,
                application.SrtCapabilityProbe):
        cls.start = lambda *args, **kwargs: None
    original_show = application.MainWindow.show
    def show(window):
        original_show(window)
        QTimer.singleShot(300, QApplication.instance().quit)
    application.MainWindow.show = show
    result = application.main()
    assert result == 0
    summary = voxel_merge(pcd_files, transforms, output_pcd, 0.01)
    summary["gui_startup_offscreen"] = True
    return summary
