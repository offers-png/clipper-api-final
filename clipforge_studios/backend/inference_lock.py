"""Serializes local speech/text model phases to avoid overlapping model residency."""
import threading

lock=threading.RLock()
