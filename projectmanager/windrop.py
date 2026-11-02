"""Native Windows Explorer drag and drop for a Tk root window."""
from __future__ import annotations

import os
import traceback
from collections import deque
from datetime import datetime
from typing import Callable

DROP_ERROR_LOG = os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"),
                              "ProjectManager", "drop_error.txt")


class NativeWindowsFileDrop:
    """Receive Explorer drops safely without calling Tk inside Win32 callbacks."""

    WM_DROPFILES = 0x0233
    WM_NCDESTROY = 0x0082
    SUBCLASS_ID = 0x504D4752  # "PMGR"

    def __init__(self, root, callback: Callable[[list[str], int, int], None]):
        self.enabled = False
        self.root = root
        self.callback = callback
        self._queue = deque()
        self._poll_id = None
        self._subclass_proc = None
        self._hwnd = None

        if os.name != "nt":
            return

        import ctypes
        from ctypes import wintypes

        self.ctypes = ctypes
        self.wintypes = wintypes
        self.user32 = ctypes.windll.user32
        self.shell32 = ctypes.windll.shell32
        self.comctl32 = ctypes.windll.comctl32

        root.update_idletasks()
        self._hwnd = wintypes.HWND(root.winfo_id())

        LRESULT = ctypes.c_ssize_t
        WPARAM = ctypes.c_size_t
        LPARAM = ctypes.c_ssize_t
        UINT_PTR = ctypes.c_size_t
        DWORD_PTR = ctypes.c_size_t
        SUBCLASSPROC = ctypes.WINFUNCTYPE(
            LRESULT, wintypes.HWND, wintypes.UINT, WPARAM, LPARAM, UINT_PTR, DWORD_PTR
        )

        self.shell32.DragAcceptFiles.argtypes = [wintypes.HWND, wintypes.BOOL]
        self.shell32.DragAcceptFiles.restype = None
        self.shell32.DragQueryFileW.argtypes = [
            wintypes.HANDLE, wintypes.UINT, wintypes.LPWSTR, wintypes.UINT
        ]
        self.shell32.DragQueryFileW.restype = wintypes.UINT
        self.shell32.DragFinish.argtypes = [wintypes.HANDLE]
        self.shell32.DragFinish.restype = None
        self.user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
        self.user32.GetCursorPos.restype = wintypes.BOOL

        self.comctl32.SetWindowSubclass.argtypes = [
            wintypes.HWND, SUBCLASSPROC, UINT_PTR, DWORD_PTR
        ]
        self.comctl32.SetWindowSubclass.restype = wintypes.BOOL
        self.comctl32.RemoveWindowSubclass.argtypes = [
            wintypes.HWND, SUBCLASSPROC, UINT_PTR
        ]
        self.comctl32.RemoveWindowSubclass.restype = wintypes.BOOL
        self.comctl32.DefSubclassProc.argtypes = [
            wintypes.HWND, wintypes.UINT, WPARAM, LPARAM
        ]
        self.comctl32.DefSubclassProc.restype = LRESULT

        def subclass_proc(hwnd, msg, wparam, lparam, subclass_id, ref_data):
            if msg == self.WM_DROPFILES:
                hdrop = wintypes.HANDLE(wparam)
                try:
                    paths = self._read_paths(hdrop)
                    point = wintypes.POINT()
                    if not self.user32.GetCursorPos(ctypes.byref(point)):
                        point.x = self.root.winfo_rootx()
                        point.y = self.root.winfo_rooty()
                    self._queue.append(("drop", paths, int(point.x), int(point.y)))
                except BaseException as exc:
                    self._queue.append(("error", repr(exc)))
                finally:
                    try:
                        self.shell32.DragFinish(hdrop)
                    except BaseException:
                        pass
                return 0

            result = self.comctl32.DefSubclassProc(hwnd, msg, wparam, lparam)
            if msg == self.WM_NCDESTROY:
                self.enabled = False
            return result

        self._subclass_proc = SUBCLASSPROC(subclass_proc)
        if not self.comctl32.SetWindowSubclass(
            self._hwnd, self._subclass_proc, self.SUBCLASS_ID, 0
        ):
            raise ctypes.WinError()

        self.shell32.DragAcceptFiles(self._hwnd, True)
        self.enabled = True
        self._poll_id = root.after(35, self._poll)

    def _read_paths(self, hdrop) -> list[str]:
        count = int(self.shell32.DragQueryFileW(hdrop, 0xFFFFFFFF, None, 0))
        count = min(max(count, 0), 10000)
        paths = []
        for index in range(count):
            length = int(self.shell32.DragQueryFileW(hdrop, index, None, 0))
            if length <= 0:
                continue
            buffer = self.ctypes.create_unicode_buffer(length + 1)
            copied = self.shell32.DragQueryFileW(hdrop, index, buffer, length + 1)
            if copied:
                paths.append(buffer.value)
        return paths

    def _poll(self):
        if not self.enabled:
            return
        try:
            while self._queue:
                item = self._queue.popleft()
                if item[0] == "drop":
                    _, paths, x, y = item
                    self.callback(paths, x, y)
                else:
                    self._write_error(item[1])
        except Exception:
            self._write_error(traceback.format_exc())
        finally:
            if self.enabled:
                try:
                    self._poll_id = self.root.after(35, self._poll)
                except Exception:
                    self.enabled = False

    @staticmethod
    def _write_error(details: str):
        try:
            os.makedirs(os.path.dirname(DROP_ERROR_LOG), exist_ok=True)
            with open(DROP_ERROR_LOG, "a", encoding="utf-8") as fh:
                fh.write(f"\n[{datetime.now():%Y-%m-%d %H:%M:%S}]\n{details}\n")
        except Exception:
            pass

    def close(self):
        if not self.enabled:
            return
        self.enabled = False
        try:
            if self._poll_id is not None:
                self.root.after_cancel(self._poll_id)
        except Exception:
            pass
        try:
            self.shell32.DragAcceptFiles(self._hwnd, False)
        except Exception:
            pass
        try:
            self.comctl32.RemoveWindowSubclass(
                self._hwnd, self._subclass_proc, self.SUBCLASS_ID
            )
        except Exception:
            pass
