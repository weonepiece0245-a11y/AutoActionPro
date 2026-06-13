package com.example.data

import androidx.room.*
import kotlinx.coroutines.flow.Flow

@Dao
interface AutomationDao {

    // --- RULES ---
    @Query("SELECT * FROM automation_rules ORDER BY orderIndex ASC, id ASC")
    fun getAllRulesFlow(): Flow<List<AutomationRule>>

    @Query("SELECT * FROM automation_rules WHERE isEnabled = 1 ORDER BY orderIndex ASC, id ASC")
    fun getEnabledRules(): List<AutomationRule>

    @Query("SELECT * FROM automation_rules WHERE isEnabled = 1 ORDER BY orderIndex ASC, id ASC")
    fun getEnabledRulesFlow(): Flow<List<AutomationRule>>

    @Insert(onConflict = OnConflictStrategy.REPLACE)
    suspend fun insertRule(rule: AutomationRule)

    @Update
    suspend fun updateRule(rule: AutomationRule)

    @Delete
    suspend fun deleteRule(rule: AutomationRule)

    @Query("UPDATE automation_rules SET timesExecuted = timesExecuted + 1 WHERE id = :ruleId")
    suspend fun incrementRuleExecutionCount(ruleId: Int)

    // --- LOGS ---
    @Query("SELECT * FROM automation_logs ORDER BY timestamp DESC LIMIT 200")
    fun getRecentLogsFlow(): Flow<List<AutomationLog>>

    @Insert(onConflict = OnConflictStrategy.REPLACE)
    suspend fun insertLog(log: AutomationLog)

    @Query("DELETE FROM automation_logs")
    suspend fun clearAllLogs()
}
