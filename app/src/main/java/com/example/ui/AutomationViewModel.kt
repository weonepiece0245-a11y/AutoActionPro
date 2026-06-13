package com.example.ui

import android.app.Application
import android.content.Context
import android.provider.Settings
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import com.example.RapidAccessibilityService
import com.example.ServiceState
import com.example.data.AppDatabase
import com.example.data.AutomationRepository
import com.example.data.AutomationRule
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.stateIn
import kotlinx.coroutines.launch

class AutomationViewModel(application: Application) : AndroidViewModel(application) {

    private val repository: AutomationRepository
    
    val allRules: StateFlow<List<AutomationRule>>
    val recentLogs: StateFlow<List<com.example.data.AutomationLog>>

    init {
        val db = AppDatabase.getDatabase(application)
        repository = AutomationRepository(db.automationDao())
        
        allRules = repository.allRules.stateIn(
            scope = viewModelScope,
            started = SharingStarted.WhileSubscribed(5000),
            initialValue = emptyList()
        )
        
        recentLogs = repository.recentLogs.stateIn(
            scope = viewModelScope,
            started = SharingStarted.WhileSubscribed(5000),
            initialValue = emptyList()
        )

        // Seed initial rules if empty to make the initial user experience perfect
        seedInitialRulesIfEmpty()
    }

    private fun seedInitialRulesIfEmpty() {
        viewModelScope.launch {
            // Check asynchronously
            val current = repository.allRules.stateIn(viewModelScope).value
            if (current.isEmpty()) {
                // Let's seed standard handy rules so the page isn't blank
                repository.insertRule(
                    AutomationRule(
                        name = "自动跳过广告",
                        targetType = "TEXT",
                        targetValue = "跳过",
                        actionType = "CLICK",
                        delayMs = 0,
                        orderIndex = 1
                    )
                )
                repository.insertRule(
                    AutomationRule(
                        name = "自动同意协议",
                        targetType = "TEXT",
                        targetValue = "同意并继续",
                        actionType = "CLICK",
                        delayMs = 0,
                        orderIndex = 2
                    )
                )
                repository.insertRule(
                    AutomationRule(
                        name = "自动进入下一步",
                        targetType = "TEXT",
                        targetValue = "下一步",
                        actionType = "CLICK",
                        delayMs = 500,
                        orderIndex = 3
                    )
                )
            }
        }
    }

    fun addRule(rule: AutomationRule) {
        viewModelScope.launch {
            repository.insertRule(rule)
            repository.logInfo("添加了新规则: ${rule.name}")
        }
    }

    fun updateRule(rule: AutomationRule) {
        viewModelScope.launch {
            repository.updateRule(rule)
        }
    }

    fun deleteRule(rule: AutomationRule) {
        viewModelScope.launch {
            repository.deleteRule(rule)
            repository.logWarning("删除了规则: ${rule.name}")
        }
    }

    fun toggleRule(rule: AutomationRule) {
        viewModelScope.launch {
            val updated = rule.copy(isEnabled = !rule.isEnabled)
            repository.updateRule(updated)
            repository.logInfo("已${if (updated.isEnabled) "开启" else "关闭"}规则: ${rule.name}")
        }
    }

    fun clearAllLogs() {
        viewModelScope.launch {
            repository.clearLogs()
            ServiceState.clearLiveLogs()
        }
    }

    fun updateInterval(intervalMs: Long) {
        ServiceState.scanIntervalMs = intervalMs
    }

    fun toggleAutoScan() {
        ServiceState.isAutoScanActive = !ServiceState.isAutoScanActive
        viewModelScope.launch {
            repository.logInfo(
                if (ServiceState.isAutoScanActive) "▶️ 启动120Hz极速自动扫描模拟" 
                else "⏸️ 暂停自动扫描模拟"
            )
        }
    }

    // Checking service configuration state
    fun checkServiceStatus(context: Context): Boolean {
        val enabled = isAccessibilityServiceEnabled(context, RapidAccessibilityService::class.java)
        ServiceState.isServiceConnected = enabled
        return enabled
    }

    private fun isAccessibilityServiceEnabled(context: Context, service: Class<*>): Boolean {
        val expectedComponentName = "${context.packageName}/${service.name}"
        val settingValue = Settings.Secure.getString(
            context.contentResolver,
            Settings.Secure.ENABLED_ACCESSIBILITY_SERVICES
        ) ?: return false
        
        val colonSplitter = settingValue.split(":")
        for (component in colonSplitter) {
            if (component.equals(expectedComponentName, ignoreCase = true)) {
                return true
            }
        }
        return false
    }
}
