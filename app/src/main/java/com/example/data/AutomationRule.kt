package com.example.data

import androidx.room.Entity
import androidx.room.PrimaryKey
import java.io.Serializable

@Entity(tableName = "automation_rules")
data class AutomationRule(
    @PrimaryKey(autoGenerate = true) val id: Int = 0,
    val name: String,
    val targetType: String, // "TEXT" or "ID"
    val targetValue: String, // Text on screen (e.g. "Skip") or Node View ID (e.g. "com.example:id/btn_next")
    val actionType: String, // "CLICK", "LONG_CLICK", "SCROLL_FORWARD", "SCROLL_BACKWARD", "INPUT_TEXT"
    val inputValue: String = "", // Text to fill if actionType is "INPUT_TEXT"
    val isEnabled: Boolean = true,
    val delayMs: Long = 0, // Delay before executing the command in milliseconds
    val timesExecuted: Int = 0, // Number of times successfully executed
    val orderIndex: Int = 0 // Priority order
) : Serializable
