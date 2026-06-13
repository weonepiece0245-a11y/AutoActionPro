package com.example

import android.accessibilityservice.AccessibilityService
import android.accessibilityservice.GestureDescription
import android.graphics.Path
import android.graphics.Rect
import android.os.Bundle
import android.view.accessibility.AccessibilityEvent
import android.view.accessibility.AccessibilityNodeInfo
import com.example.data.AppDatabase
import com.example.data.AutomationRepository
import com.example.data.AutomationRule
import kotlinx.coroutines.*
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import kotlin.coroutines.suspendCoroutine
import kotlin.coroutines.resume

class RapidAccessibilityService : AccessibilityService() {

    private val serviceJob = SupervisorJob()
    private val serviceScope = CoroutineScope(Dispatchers.Main + serviceJob)
    private var scanJob: Job? = null
    
    private lateinit var repository: AutomationRepository
    private val lastExecutedTimeMap = mutableMapOf<Int, Long>()
    private val timeFormat = SimpleDateFormat("HH:mm:ss.SSS", Locale.getDefault())

    override fun onCreate() {
        super.onCreate()
        val dao = AppDatabase.getDatabase(this).automationDao()
        repository = AutomationRepository(dao)
    }

    override fun onServiceConnected() {
        super.onServiceConnected()
        ServiceState.isServiceConnected = true
        logToSystem("自动引擎已连接 Ready, 正在监听...")

        // Start High-frequency simulation engine loop
        startAutomationLoop()
    }

    override fun onAccessibilityEvent(event: AccessibilityEvent?) {
        if (event == null) return
        if (event.eventType == AccessibilityEvent.TYPE_WINDOW_STATE_CHANGED ||
            event.eventType == AccessibilityEvent.TYPE_WINDOW_CONTENT_CHANGED) {
            // Trigger rapid check if auto scan is active
            if (ServiceState.isAutoScanActive) {
                serviceScope.launch {
                    scanAndExecute()
                }
            }
        }
    }

    override fun onInterrupt() {
        logToSystem("自动辅助引擎被中断")
    }

    override fun onUnbind(intent: android.content.Intent?): Boolean {
        ServiceState.isServiceConnected = false
        stopAutomationLoop()
        serviceScope.cancel()
        return super.onUnbind(intent)
    }

    override fun onDestroy() {
        ServiceState.isServiceConnected = false
        stopAutomationLoop()
        serviceScope.cancel()
        super.onDestroy()
    }

    private fun startAutomationLoop() {
        scanJob?.cancel()
        scanJob = serviceScope.launch(Dispatchers.Main) {
            while (isActive) {
                if (ServiceState.isAutoScanActive) {
                    scanAndExecute()
                }
                delay(ServiceState.scanIntervalMs)
            }
        }
    }

    private fun stopAutomationLoop() {
        scanJob?.cancel()
        scanJob = null
    }

    private val clickMutex = java.util.concurrent.atomic.AtomicBoolean(false)

    private suspend fun scanAndExecute() {
        if (clickMutex.get()) return
        
        val activeWindowNode = rootInActiveWindow ?: return
        
        val enabledRules = repository.getEnabledRulesSync()
        if (enabledRules.isEmpty()) {
            try {
                activeWindowNode.recycle()
            } catch (ignored: Exception) {}
            return
        }

        val currentTime = System.currentTimeMillis()
        
        for (rule in enabledRules) {
            val lastExec = lastExecutedTimeMap[rule.id] ?: 0L
            val elapsed = currentTime - lastExec
            
            // Limit minimum cooldown per rule to prevent lock-up; default custom delay + 2s safeguard cooldown
            val minCooldown = 2000L
            if (elapsed < (rule.delayMs + minCooldown)) {
                continue
            }

            val matchedNodes = mutableListOf<AccessibilityNodeInfo>()
            findNodesRecursive(activeWindowNode, rule, matchedNodes)

            if (matchedNodes.isNotEmpty()) {
                val targetNode = matchedNodes.first()
                
                clickMutex.set(true)
                try {
                    val actionName = rule.actionType
                    val ruleName = rule.name.ifEmpty { "规则 #${rule.id}" }
                    
                    logToSystem("匹配成功: \"${ruleName}\" [${rule.targetType}: ${rule.targetValue}] -> 执行 $actionName")
                    
                    val success = performActionOnNode(targetNode, rule)
                    if (success) {
                        lastExecutedTimeMap[rule.id] = System.currentTimeMillis()
                        repository.incrementRuleExecution(rule.id)
                        repository.logSuccess("成功执行动作 [${actionName}]: ${ruleName}")
                    } else {
                        repository.logError("执行动作失败 [${actionName}]: ${ruleName}")
                    }
                } catch (e: Exception) {
                    repository.logError("执行遭遇异常: ${e.message}")
                } finally {
                    matchedNodes.forEach { try { it.recycle() } catch (ignored: Exception) {} }
                    clickMutex.set(false)
                }
                // Stop processing other rules in this frame to let layouts refresh
                break
            }
        }
        
        try {
            activeWindowNode.recycle()
        } catch (ignored: Exception) {}
    }

    private fun findNodesRecursive(
        node: AccessibilityNodeInfo?, 
        rule: AutomationRule, 
        resultList: MutableList<AccessibilityNodeInfo>
    ) {
        if (node == null) return
        
        var isMatch = false
        if (rule.targetType == "TEXT") {
            val text = node.text?.toString() ?: ""
            val contentDesc = node.contentDescription?.toString() ?: ""
            if (text.contains(rule.targetValue, ignoreCase = true) || 
                contentDesc.contains(rule.targetValue, ignoreCase = true)) {
                isMatch = true
            }
        } else if (rule.targetType == "ID") {
            val viewId = node.viewIdResourceName ?: ""
            if (viewId.equals(rule.targetValue, ignoreCase = true) || 
                viewId.endsWith("/" + rule.targetValue, ignoreCase = true)) {
                isMatch = true
            }
        }
        
        if (isMatch) {
            resultList.add(AccessibilityNodeInfo.obtain(node))
        }
        
        for (i in 0 until node.childCount) {
            val child = node.getChild(i)
            if (child != null) {
                findNodesRecursive(child, rule, resultList)
            }
        }
    }

    private suspend fun performActionOnNode(node: AccessibilityNodeInfo, rule: AutomationRule): Boolean {
        val rect = Rect()
        node.getBoundsInScreen(rect)
        
        if (rect.isEmpty) return false
        
        val x = rect.centerX().toFloat()
        val y = rect.centerY().toFloat()

        when (rule.actionType) {
            "CLICK" -> {
                return if (node.isClickable) {
                    val clicked = node.performAction(AccessibilityNodeInfo.ACTION_CLICK)
                    if (clicked) {
                        logToSystem("节点触发 programmatic 点击")
                        true
                    } else {
                        dispatchPhysicalClick(x, y)
                    }
                } else {
                    dispatchPhysicalClick(x, y)
                }
            }
            "LONG_CLICK" -> {
                if (node.isLongClickable) {
                    val longClicked = node.performAction(AccessibilityNodeInfo.ACTION_LONG_CLICK)
                    if (longClicked) {
                        logToSystem("节点触发 programmatic 长按")
                        return true
                    }
                }
                return dispatchPhysicalLongClick(x, y)
            }
            "INPUT_TEXT" -> {
                node.performAction(AccessibilityNodeInfo.ACTION_FOCUS)
                val arguments = Bundle()
                arguments.putCharSequence(AccessibilityNodeInfo.ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE, rule.inputValue)
                val success = node.performAction(AccessibilityNodeInfo.ACTION_SET_TEXT, arguments)
                if (success) {
                    logToSystem("输入框自动填充文本成功")
                    return true
                }
                return false
            }
            "SCROLL_FORWARD" -> {
                val scrolled = node.performAction(AccessibilityNodeInfo.ACTION_SCROLL_FORWARD)
                if (scrolled) {
                    logToSystem("元素 programmatic 向前翻页")
                    return true
                } else {
                    val metrics = resources.displayMetrics
                    val w = metrics.widthPixels
                    val h = metrics.heightPixels
                    return dispatchPhysicalSwipe(w / 2f, h * 0.8f, w / 2f, h * 0.2f, 250L)
                }
            }
            "SCROLL_BACKWARD" -> {
                val scrolled = node.performAction(AccessibilityNodeInfo.ACTION_SCROLL_BACKWARD)
                if (scrolled) {
                    logToSystem("元素 programmatic 向后翻页")
                    return true
                } else {
                    val metrics = resources.displayMetrics
                    val w = metrics.widthPixels
                    val h = metrics.heightPixels
                    return dispatchPhysicalSwipe(w / 2f, h * 0.2f, w / 2f, h * 0.8f, 250L)
                }
            }
            else -> return false
        }
    }

    private suspend fun dispatchPhysicalClick(x: Float, y: Float): Boolean {
        return suspendCoroutine { continuation ->
            val path = Path()
            path.moveTo(x, y)
            val stroke = GestureDescription.StrokeDescription(path, 0, 40) // Fast 40ms stroke duration
            val builder = GestureDescription.Builder()
            builder.addStroke(stroke)
            
            try {
                dispatchGesture(builder.build(), object : GestureResultCallback() {
                    override fun onCompleted(gestureDescription: GestureDescription?) {
                        logToSystem("极速硬件模拟点击: (${x.toInt()}, ${y.toInt()})")
                        continuation.resume(true)
                    }
                    override fun onCancelled(gestureDescription: GestureDescription?) {
                        logToSystem("硬件模拟点击已取消")
                        continuation.resume(false)
                    }
                }, null)
            } catch (e: Exception) {
                continuation.resume(false)
            }
        }
    }

    private suspend fun dispatchPhysicalLongClick(x: Float, y: Float): Boolean {
        return suspendCoroutine { continuation ->
            val path = Path()
            path.moveTo(x, y)
            val stroke = GestureDescription.StrokeDescription(path, 0, 800)
            val builder = GestureDescription.Builder()
            builder.addStroke(stroke)
            
            try {
                dispatchGesture(builder.build(), object : GestureResultCallback() {
                    override fun onCompleted(gestureDescription: GestureDescription?) {
                        logToSystem("物理长按完成: (${x.toInt()}, ${y.toInt()})")
                        continuation.resume(true)
                    }
                    override fun onCancelled(gestureDescription: GestureDescription?) {
                        continuation.resume(false)
                    }
                }, null)
            } catch (e: Exception) {
                continuation.resume(false)
            }
        }
    }

    private suspend fun dispatchPhysicalSwipe(x1: Float, y1: Float, x2: Float, y2: Float, duration: Long): Boolean {
        return suspendCoroutine { continuation ->
            val path = Path()
            path.moveTo(x1, y1)
            path.lineTo(x2, y2)
            val stroke = GestureDescription.StrokeDescription(path, 0, duration)
            val builder = GestureDescription.Builder()
            builder.addStroke(stroke)
            
            try {
                dispatchGesture(builder.build(), object : GestureResultCallback() {
                    override fun onCompleted(gestureDescription: GestureDescription?) {
                        logToSystem("物理滑动完成: (${x1.toInt()}, ${y1.toInt()}) -> (${x2.toInt()}, ${y2.toInt()})")
                        continuation.resume(true)
                    }
                    override fun onCancelled(gestureDescription: GestureDescription?) {
                        continuation.resume(false)
                    }
                }, null)
            } catch (e: Exception) {
                continuation.resume(false)
            }
        }
    }

    private fun logToSystem(msg: String) {
        val timeStr = timeFormat.format(Date())
        val msgWithTime = "[$timeStr] $msg"
        ServiceState.addLiveLog(msgWithTime)
        serviceScope.launch {
            repository.insertLog(msg, "INFO")
        }
    }
}
