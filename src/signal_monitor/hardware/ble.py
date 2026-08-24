#!/usr/bin/env python3
"""BLE 掃描的共用包裝：把底層例外翻成使用者看得懂的說明。

`muselsl.list_muses()` 底下是 bleak / BlueZ，藍牙沒開、服務沒起、
或使用者沒有權限時會丟出各種例外。這些例外原本會一路往上炸成
traceback（在互動式控制台裡甚至會直接把選單殺掉），對使用者毫無幫助。

這裡集中處理，五個呼叫端（list_devices / monitor_raw / record_csv /
overall_process / cli）共用同一套訊息。
"""


def describe_ble_error(exc):
    """把 BLE 例外翻成一句可行動的說明。"""
    name = type(exc).__name__
    text = str(exc)

    if "BluetoothNotAvailable" in name or "No powered Bluetooth adapters" in text:
        return (
            "找不到已開啟的藍牙介面卡 —— 藍牙可能是關閉的。\n"
            "  請先開啟藍牙再試一次：\n"
            "    rfkill unblock bluetooth\n"
            "    bluetoothctl power on\n"
            "  確認狀態：bluetoothctl show"
        )
    if "org.freedesktop.DBus" in text or "ServiceUnknown" in text:
        return (
            "連不上系統的藍牙服務（D-Bus）。\n"
            "  試著重啟服務：sudo systemctl restart bluetooth"
        )
    if "NotAuthorized" in text or "AccessDenied" in text or "Permission" in text:
        return (
            "沒有操作藍牙的權限。\n"
            "  把使用者加入 bluetooth 群組後重新登入：\n"
            "    sudo usermod -aG bluetooth $USER"
        )
    if "BleakDBusError" in name or "org.bluez" in text:
        return (
            f"藍牙服務回報錯誤：{text}\n"
            "  試著重啟服務：sudo systemctl restart bluetooth"
        )
    return f"{name}: {text}"


def scan_muses(backend="bleak"):
    """掃描 MUSE 裝置。

    回傳 (裝置清單, 錯誤說明)。掃描成功時錯誤說明為 None —— 注意「掃描成功
    但沒找到裝置」會回傳 ([], None)，與「掃描本身失敗」是不同的情況，
    呼叫端要分開處理（前者是頭帶沒開機，後者是電腦端的藍牙有問題）。
    """
    from muselsl import list_muses
    try:
        return list_muses(backend=backend) or [], None
    except Exception as exc:                      # noqa: BLE001 - 底層例外種類很雜，一律翻譯
        return [], describe_ble_error(exc)


NO_DEVICE_HINT = (
    "找不到任何 MUSE 裝置。請確認：\n"
    "  1) 頭帶已開機，且 LED 正在閃爍（未與手機 App 連線）\n"
    "  2) 頭帶距離電腦夠近\n"
    "  3) 電腦藍牙已開啟（bluetoothctl show）"
)
