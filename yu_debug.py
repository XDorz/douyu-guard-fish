"""开启诊断采集的 GUI 入口；与普通版共享外部 config.json。"""
from yu_gui import main


if __name__ == "__main__":
    main(debug=True)
