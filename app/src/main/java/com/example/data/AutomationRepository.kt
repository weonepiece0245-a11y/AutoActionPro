package com.example.data

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.withContext

class AutomationRepository(private val dao: AutomationDao) {

    val allRules: Flow<List<AutomationRule>> = dao.getAllRulesFlow()
    val enabledRulesFlow: Flow<List<AutomationRule>> = dao.getEnabledRulesFlow()
    val recentLogs: Flow<List<AutomationLog>> = dao.getRecentLogsFlow()

    suspend fun insertRule(rule: AutomationRule) = withContext(Dispatchers.IO) {
        dao.insertRule(rule)
    }

    suspend fun updateRule(rule: AutomationRule) = withContext(Dispatchers.IO) {
        dao.updateRule(rule)
    }

    suspend fun deleteRule(rule: AutomationRule) = withContext(Dispatchers.IO) {
        dao.deleteRule(rule)
    }

    suspend fun incrementRuleExecution(ruleId: Int) = withContext(Dispatchers.IO) {
        dao.incrementRuleExecutionCount(ruleId)
    }

    suspend fun getEnabledRulesSync(): List<AutomationRule> = withContext(Dispatchers.IO) {
        dao.getEnabledRules()
    }

    suspend fun logInfo(message: String) {
        insertLog(message, "INFO")
    }

    suspend fun logSuccess(message: String) {
        insertLog(message, "SUCCESS")
    }

    suspend fun logWarning(message: String) {
        insertLog(message, "WARNING")
    }

    suspend fun logError(message: String) {
        insertLog(message, "ERROR")
    }

    suspend fun insertLog(message: String, type: String) = withContext(Dispatchers.IO) {
        dao.insertLog(AutomationLog(message = message, type = type))
    }

    suspend fun clearLogs() = withContext(Dispatchers.IO) {
        dao.clearAllLogs()
    }
}
