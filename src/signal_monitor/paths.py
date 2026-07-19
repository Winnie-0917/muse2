"""集中管理專案路徑。

重構前，各腳本都用 `os.path.dirname(os.path.abspath(__file__))` 當作專案根目錄，
再往下找 `Data/`、`FFT/`、`EI/`、`FAA/` 等輸出資料夾。搬進 `src/signal_monitor/`
套件後，各模組的 `__file__` 不再位於專案根目錄，因此這裡統一計算一次根目錄，
讓所有輸出位置與重構前完全一致（仍在專案根目錄，而非套件內）。

本檔位於 ``<專案根>/src/signal_monitor/paths.py``，往上三層即專案根目錄。
"""
import os

# paths.py -> signal_monitor/ -> src/ -> 專案根目錄
PROJECT_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
