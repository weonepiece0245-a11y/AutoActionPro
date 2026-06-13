package com.example

import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue

object ServiceState {
    // Observable states in Compose directly
    var isServiceConnected by mutableStateOf(false)
    var isAutoScanActive by mutableStateOf(false)
    var scanIntervalMs by mutableStateOf(100L) // Support ultra-fast 50ms (for 120Hz responsiveness) or standard 100ms
    
    // Simple thread-safe cache for fast in-progress logs to display immediately
    private val _liveLogs = mutableListOf<String>()
    
    val liveLogs: List<String>
        get() = synchronized(_liveLogs) { _liveLogs.toList() }

    fun addLiveLog(message: String) {
        synchronized(_liveLogs) {
            _liveLogs.add(0, message) // Insert at top
            if (_liveLogs.size > 100) {
                _liveLogs.removeAt(_liveLogs.size - 1)
            }
        }
    }

    fun clearLiveLogs() {
        synchronized(_liveLogs) {
            _liveLogs.clear()
        }
    }
}
