"""signal_monitor：MUSE 2 原始 EEG 擷取、每秒 FFT、專注度 EI 與前額 alpha 不對稱 FAA。

原本散在專案根目錄的各腳本重構為一個套件，程式邏輯不變——只是改成套件式匯入，
並以 ``python -m signal_monitor``（互動式控制台）或 ``python -m signal_monitor.<子模組>``
的方式執行。
"""
__version__ = "0.1.0"
