package com.example.ui

import android.content.Intent
import android.provider.Settings
import androidx.compose.animation.*
import androidx.compose.animation.core.*
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.graphics.drawscope.withTransform
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.window.Dialog
import com.example.ServiceState
import com.example.data.AutomationRule
import com.example.ui.theme.*
import kotlinx.coroutines.delay

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun DashboardScreen(
    viewModel: AutomationViewModel,
    modifier: Modifier = Modifier
) {
    val context = LocalContext.current
    val rules by viewModel.allRules.collectAsState()
    val logs by viewModel.recentLogs.collectAsState()
    
    var showAddDialog by remember { mutableStateOf(false) }
    var selectedTab by remember { mutableStateOf(0) } // 0: 规则管理, 1: 运行日志, 2: 引擎参数
    
    // Periodically sync the system status indicator
    LaunchedEffect(Unit) {
        while (true) {
            viewModel.checkServiceStatus(context)
            delay(1500)
        }
    }

    Scaffold(
        modifier = modifier
            .fillMaxSize()
            .background(MaterialTheme.colorScheme.background),
        bottomBar = {
            // Safe, full-bleed Material 3 Navigation Bar matching Bold Typography spec
            NavigationBar(
                containerColor = MaterialTheme.colorScheme.surface,
                tonalElevation = 6.dp,
                modifier = Modifier
                    .windowInsetsPadding(WindowInsets.navigationBars)
                    .border(BorderStroke(1.5.dp, BoldIceBlue), RoundedCornerShape(topStart = 24.dp, topEnd = 24.dp))
                    .clip(RoundedCornerShape(topStart = 24.dp, topEnd = 24.dp))
            ) {
                NavigationBarItem(
                    selected = selectedTab == 0,
                    onClick = { selectedTab = 0 },
                    icon = { Icon(Icons.Default.List, contentDescription = "规则") },
                    label = { 
                        Text(
                            "规则管理",
                            style = MaterialTheme.typography.labelSmall.copy(fontWeight = FontWeight.ExtraBold)
                        ) 
                    },
                    colors = NavigationBarItemDefaults.colors(
                        selectedIconColor = BoldNavy,
                        selectedTextColor = BoldNavy,
                        indicatorColor = BoldIceBlue,
                        unselectedIconColor = BoldSteel.copy(alpha = 0.6f),
                        unselectedTextColor = BoldSteel.copy(alpha = 0.6f)
                    )
                )
                NavigationBarItem(
                    selected = selectedTab == 1,
                    onClick = { selectedTab = 1 },
                    icon = { 
                        BadgedBox(badge = {
                            if (ServiceState.liveLogs.isNotEmpty() && selectedTab != 1) {
                                Badge(containerColor = BoldSalmon)
                            }
                        }) {
                            Icon(Icons.Default.Info, contentDescription = "日志") 
                        }
                    },
                    label = { 
                        Text(
                            "运行日志",
                            style = MaterialTheme.typography.labelSmall.copy(fontWeight = FontWeight.ExtraBold)
                        ) 
                    },
                    colors = NavigationBarItemDefaults.colors(
                        selectedIconColor = BoldNavy,
                        selectedTextColor = BoldNavy,
                        indicatorColor = BoldIceBlue,
                        unselectedIconColor = BoldSteel.copy(alpha = 0.6f),
                        unselectedTextColor = BoldSteel.copy(alpha = 0.6f)
                    )
                )
                NavigationBarItem(
                    selected = selectedTab == 2,
                    onClick = { selectedTab = 2 },
                    icon = { Icon(Icons.Default.Settings, contentDescription = "配置") },
                    label = { 
                        Text(
                            "引擎设置",
                            style = MaterialTheme.typography.labelSmall.copy(fontWeight = FontWeight.ExtraBold)
                        ) 
                    },
                    colors = NavigationBarItemDefaults.colors(
                        selectedIconColor = BoldNavy,
                        selectedTextColor = BoldNavy,
                        indicatorColor = BoldIceBlue,
                        unselectedIconColor = BoldSteel.copy(alpha = 0.6f),
                        unselectedTextColor = BoldSteel.copy(alpha = 0.6f)
                    )
                )
            }
        },
        floatingActionButton = {
            if (selectedTab == 0) {
                FloatingActionButton(
                    onClick = { showAddDialog = true },
                    containerColor = BoldNavy,
                    contentColor = Color.White,
                    elevation = FloatingActionButtonDefaults.elevation(6.dp),
                    modifier = Modifier.testTag("add_rule_fab")
                ) {
                    Icon(imageVector = Icons.Default.Add, contentDescription = "新建规则", modifier = Modifier.size(28.dp))
                }
            }
        },
        contentWindowInsets = WindowInsets.safeDrawing
    ) { innerPadding ->
        Column(
            modifier = Modifier
                .fillMaxSize()
                .padding(innerPadding)
                .background(MaterialTheme.colorScheme.background)
        ) {
            // Simulated Status Bar & Bold Expressive Header Section (Replaces topBar)
            Column(
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(horizontal = 20.dp, vertical = 14.dp)
            ) {
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.SpaceBetween,
                    verticalAlignment = Alignment.CenterVertically
                ) {
                    Box(
                        modifier = Modifier
                            .background(BoldIceBlue, RoundedCornerShape(100.dp))
                            .padding(horizontal = 14.dp, vertical = 5.dp)
                    ) {
                        Text(
                            text = "API 36 READY",
                            style = MaterialTheme.typography.labelSmall.copy(
                                fontWeight = FontWeight.Black,
                                color = BoldNavy,
                                letterSpacing = 1.2.sp
                            )
                        )
                    }

                    // Centered cursor clicking motif simulate dot indicator
                    IconButton(
                        onClick = { viewModel.checkServiceStatus(context) },
                        modifier = Modifier
                            .size(36.dp)
                            .background(BoldSoftGrey, RoundedCornerShape(100.dp))
                            .testTag("refresh_service_btn")
                    ) {
                        Icon(
                            imageVector = Icons.Default.Refresh,
                            contentDescription = "查看服务状态",
                            tint = BoldNavy,
                            modifier = Modifier.size(18.dp)
                        )
                    }
                }

                Spacer(modifier = Modifier.height(14.dp))

                Text(
                    text = "AUTO\nASSIST",
                    style = MaterialTheme.typography.displayLarge.copy(
                        color = BoldNavy,
                        lineHeight = 44.sp
                    ),
                    modifier = Modifier.testTag("app_logo_title")
                )

                Spacer(modifier = Modifier.height(8.dp))

                Text(
                    text = "全无障碍自动化高频手势控制总线。完美适配全面屏全屏绘制，零延迟免 Root。",
                    style = MaterialTheme.typography.bodyMedium.copy(
                        color = BoldSteel,
                        lineHeight = 18.sp
                    )
                )
            }

            // Accessibility service status dashboard card
            StatusHeaderCard(
                isServiceEnabled = ServiceState.isServiceConnected,
                isScanActive = ServiceState.isAutoScanActive,
                onToggleScan = { viewModel.toggleAutoScan() },
                onNavigateSettings = {
                    try {
                        val intent = Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS).apply {
                            flags = Intent.FLAG_ACTIVITY_NEW_TASK
                        }
                        context.startActivity(intent)
                    } catch (e: Exception) {
                        // fallback
                    }
                }
            )

            HorizontalDivider(color = BoldIceBlue, thickness = 1.5.dp)

            Box(modifier = Modifier.fillMaxSize()) {
                Surface(
                    color = Color.Transparent,
                    modifier = Modifier.fillMaxSize()
                ) {
                    when (selectedTab) {
                        0 -> RulesManagementTab(
                            rules = rules,
                            onToggleRule = { viewModel.toggleRule(it) },
                            onDeleteRule = { viewModel.deleteRule(it) }
                        )
                        1 -> LogsTab(
                            liveLogs = ServiceState.liveLogs,
                            onClearLogs = { viewModel.clearAllLogs() }
                        )
                        2 -> EngineSettingsTab(
                            currentInterval = ServiceState.scanIntervalMs,
                            onSelectInterval = { viewModel.updateInterval(it) }
                        )
                    }
                }
            }
        }
    }

    // Modal dialog to add a rule
    if (showAddDialog) {
        AddRuleDialog(
            onDismiss = { showAddDialog = false },
            onConfirm = { rule ->
                viewModel.addRule(rule)
                showAddDialog = false
            }
        )
    }
}

@Composable
fun StatusHeaderCard(
    isServiceEnabled: Boolean,
    isScanActive: Boolean,
    onToggleScan: () -> Unit,
    onNavigateSettings: () -> Unit
) {
    Card(
        modifier = Modifier
            .fillMaxWidth()
            .padding(16.dp),
        colors = CardDefaults.cardColors(containerColor = BoldNavy),
        shape = RoundedCornerShape(32.dp),
        border = BorderStroke(2.dp, BoldIceBlue)
    ) {
        Column(
            modifier = Modifier
                .fillMaxWidth()
                .padding(20.dp),
            verticalArrangement = Arrangement.spacedBy(16.dp)
        ) {
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically
            ) {
                Column {
                    Text(
                        text = "SCREEN READER SERVICE",
                        color = BoldIceBlue.copy(alpha = 0.8f),
                        style = MaterialTheme.typography.labelSmall.copy(
                            fontWeight = FontWeight.Black,
                            letterSpacing = 1.sp
                        )
                    )
                    Spacer(modifier = Modifier.height(4.dp))
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Box(
                            modifier = Modifier
                                .size(10.dp)
                                .clip(RoundedCornerShape(50))
                                .background(if (isServiceEnabled) ActiveGreen else WarnRed)
                        )
                        Spacer(modifier = Modifier.width(8.dp))
                        Text(
                            text = if (isServiceEnabled) "引擎就绪 (Ready)" else "连接断开 (Disabled)",
                            color = Color.White,
                            style = MaterialTheme.typography.titleMedium.copy(fontWeight = FontWeight.Black)
                        )
                    }
                }

                if (!isServiceEnabled) {
                    Button(
                        onClick = onNavigateSettings,
                        colors = ButtonDefaults.buttonColors(containerColor = BoldSalmon, contentColor = BoldNavy),
                        contentPadding = PaddingValues(horizontal = 14.dp, vertical = 8.dp),
                        shape = RoundedCornerShape(12.dp),
                        modifier = Modifier.testTag("enable_service_btn")
                    ) {
                        Icon(Icons.Default.Settings, contentDescription = null, modifier = Modifier.size(16.dp))
                        Spacer(modifier = Modifier.width(6.dp))
                        Text("去授权", style = MaterialTheme.typography.labelSmall.copy(fontWeight = FontWeight.Black))
                    }
                } else {
                    Row(
                        modifier = Modifier
                            .background(ActiveGreen.copy(alpha = 0.15f), RoundedCornerShape(12.dp))
                            .padding(horizontal = 12.dp, vertical = 6.dp),
                        verticalAlignment = Alignment.CenterVertically
                    ) {
                        Text(
                            text = "120Hz 极刷",
                            color = ActiveGreen,
                            style = MaterialTheme.typography.labelSmall,
                            fontWeight = FontWeight.Black
                        )
                    }
                }
            }

            Row(
                modifier = Modifier
                    .fillMaxWidth()
                    .background(Color.White.copy(alpha = 0.08f), RoundedCornerShape(18.dp))
                    .padding(14.dp),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically
            ) {
                // Interactive Radar Simulator Graphics Animation
                Box(modifier = Modifier.size(54.dp), contentAlignment = Alignment.Center) {
                    ScanningRadarEffect(isSearching = isScanActive && isServiceEnabled)
                }

                Column(modifier = Modifier.weight(1f).padding(horizontal = 14.dp)) {
                    Text(
                        text = "手动模拟极速巡航",
                        color = Color.White,
                        style = MaterialTheme.typography.bodyMedium.copy(fontWeight = FontWeight.ExtraBold)
                    )
                    Text(
                        text = if (isScanActive) "正在以 ${ServiceState.scanIntervalMs}ms 的频率检索匹配" else "引擎待命，启动后将扫描并自动操作",
                        color = BoldIceBlue.copy(alpha = 0.7f),
                        style = MaterialTheme.typography.labelSmall
                    )
                }

                Switch(
                    checked = isScanActive,
                    onCheckedChange = { 
                        if (isServiceEnabled) onToggleScan() 
                    },
                    enabled = isServiceEnabled,
                    colors = SwitchDefaults.colors(
                        checkedThumbColor = BoldNavy,
                        checkedTrackColor = ActiveGreen,
                        uncheckedThumbColor = BoldSteel,
                        uncheckedTrackColor = BoldSoftGrey.copy(alpha = 0.3f)
                    ),
                    modifier = Modifier.testTag("global_scan_switch")
                )
            }
        }
    }
}

@Composable
fun ScanningRadarEffect(isSearching: Boolean) {
    val infiniteTransition = rememberInfiniteTransition(label = "RadarTransition")
    
    val rotation by infiniteTransition.animateFloat(
        initialValue =  0f,
        targetValue = 360f,
        animationSpec = infiniteRepeatable(
            animation = tween(2200, easing = LinearEasing),
            repeatMode = RepeatMode.Restart
        ),
        label = "RadarRotation"
    )

    val pulseScale by infiniteTransition.animateFloat(
        initialValue = 0.6f,
        targetValue = 1.0f,
        animationSpec = infiniteRepeatable(
            animation = tween(1100, easing = EaseInOutSine),
            repeatMode = RepeatMode.Reverse
        ),
        label = "RadarPulse"
    )

    Canvas(modifier = Modifier.size(48.dp)) {
        val center = Offset(size.width / 2, size.height / 2)
        val radius = size.minDimension / 2

        // Base rings
        drawCircle(
            color = if (isSearching) ActiveGreen.copy(alpha = 0.25f) else BoldIceBlue.copy(alpha = 0.2f),
            radius = radius * pulseScale,
            style = Stroke(width = 1.5.dp.toPx())
        )
        drawCircle(
            color = if (isSearching) ActiveGreen.copy(alpha = 0.1f) else BoldIceBlue.copy(alpha = 0.1f),
            radius = radius * 0.5f,
            style = Stroke(width = 1.5.dp.toPx())
        )

        // Radar scanner sweep
        if (isSearching) {
            withTransform({
                rotate(degrees = rotation, pivot = center)
            }) {
                drawArc(
                    brush = Brush.sweepGradient(
                        colors = listOf(Color.Transparent, ActiveGreen.copy(alpha = 0.7f)),
                        center = center
                    ),
                    startAngle = 0f,
                    sweepAngle = 90f,
                    useCenter = true
                )
            }
        } else {
            drawCircle(
                color = BoldIceBlue,
                radius = 4.dp.toPx()
            )
        }
    }
}

@Composable
fun RulesManagementTab(
    rules: List<AutomationRule>,
    onToggleRule: (AutomationRule) -> Unit,
    onDeleteRule: (AutomationRule) -> Unit
) {
    Column(
        modifier = Modifier
            .fillMaxSize()
            .padding(horizontal = 16.dp, vertical = 8.dp)
    ) {
        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.CenterVertically
        ) {
            Text(
                text = "活跃匹配规则 (Rules)",
                color = BoldNavy,
                style = MaterialTheme.typography.titleMedium.copy(fontWeight = FontWeight.ExtraBold),
            )
            Text(
                text = "已配置 ${rules.size} 条",
                color = BoldSteel,
                style = MaterialTheme.typography.bodySmall.copy(fontWeight = FontWeight.Bold)
            )
        }

        Spacer(modifier = Modifier.height(12.dp))

        if (rules.isEmpty()) {
            Box(
                modifier = Modifier
                    .fillMaxWidth()
                    .weight(1f),
                contentAlignment = Alignment.Center
            ) {
                Column(horizontalAlignment = Alignment.CenterHorizontally) {
                    Icon(
                        imageVector = Icons.Default.Info,
                        contentDescription = null,
                        tint = Color.Gray,
                        modifier = Modifier.size(48.dp)
                    )
                    Spacer(modifier = Modifier.height(12.dp))
                    Text("暂无自动化规则", color = Color.Gray, style = MaterialTheme.typography.bodyMedium)
                    Text("点击右下角按钮添加您的第一条巡航动作", color = Color.DarkGray, style = MaterialTheme.typography.bodySmall)
                }
            }
        } else {
            LazyColumn(
                modifier = Modifier.weight(1f),
                verticalArrangement = Arrangement.spacedBy(10.dp)
            ) {
                items(rules) { rule ->
                    RuleItemCard(
                        rule = rule,
                        onToggle = { onToggleRule(rule) },
                        onDelete = { onDeleteRule(rule) }
                    )
                }
            }
        }
    }
}

@Composable
fun RuleItemCard(
    rule: AutomationRule,
    onToggle: () -> Unit,
    onDelete: () -> Unit
) {
    Card(
        modifier = Modifier
            .fillMaxWidth()
            .testTag("rule_card_${rule.id}"),
        colors = CardDefaults.cardColors(
            containerColor = if (rule.isEnabled) MaterialTheme.colorScheme.surface else MaterialTheme.colorScheme.surface.copy(alpha = 0.6f)
        ),
        shape = RoundedCornerShape(24.dp),
        border = BorderStroke(
            width = 2.dp,
            color = if (rule.isEnabled) BoldIceBlue else BoldSoftGrey.copy(alpha = 0.6f)
        ),
        elevation = CardDefaults.cardElevation(defaultElevation = 2.dp)
    ) {
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .padding(16.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.SpaceBetween
        ) {
            Column(modifier = Modifier.weight(1f)) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Box(
                        modifier = Modifier
                            .background(
                                if (rule.targetType == "TEXT") BoldIceBlue else BoldSalmon.copy(alpha = 0.4f),
                                RoundedCornerShape(8.dp)
                            )
                            .padding(horizontal = 8.dp, vertical = 3.dp)
                    ) {
                        Text(
                            text = if (rule.targetType == "TEXT") "文字" else "ID",
                            color = BoldNavy,
                            style = MaterialTheme.typography.labelSmall.copy(fontWeight = FontWeight.Black)
                        )
                    }

                    Spacer(modifier = Modifier.width(8.dp))

                    Text(
                        text = rule.name,
                        color = if (rule.isEnabled) BoldNavy else BoldSteel,
                        style = MaterialTheme.typography.titleMedium.copy(fontWeight = FontWeight.Black)
                    )
                }

                Spacer(modifier = Modifier.height(8.dp))
                
                Text(
                    text = "匹配值: \"${rule.targetValue}\"",
                    color = BoldNavy,
                    fontWeight = FontWeight.Bold,
                    style = MaterialTheme.typography.bodyMedium,
                    fontFamily = FontFamily.Monospace
                )

                Spacer(modifier = Modifier.height(8.dp))

                Row(
                    horizontalArrangement = Arrangement.spacedBy(10.dp),
                    verticalAlignment = Alignment.CenterVertically
                ) {
                    Text(
                        text = "物理动作: ${mapActionType(rule.actionType)}",
                        color = BoldSteel,
                        style = MaterialTheme.typography.labelSmall.copy(fontWeight = FontWeight.Bold)
                    )
                    
                    if (rule.inputValue.isNotEmpty()) {
                        Text(
                            text = "填充: \"${rule.inputValue}\"",
                            color = BoldSteel,
                            style = MaterialTheme.typography.labelSmall.copy(fontWeight = FontWeight.Bold)
                        )
                    }

                    if (rule.delayMs > 0) {
                        Text(
                            text = "延迟: ${rule.delayMs}ms",
                            color = BoldSteel,
                            style = MaterialTheme.typography.labelSmall.copy(fontWeight = FontWeight.Bold)
                        )
                    }
                    
                    Text(
                        text = "执行: ${rule.timesExecuted} 次",
                        color = if (rule.timesExecuted > 0) ActiveGreen else BoldSteel.copy(alpha = 0.5f),
                        style = MaterialTheme.typography.labelSmall.copy(fontWeight = FontWeight.Black)
                    )
                }
            }

            Row(verticalAlignment = Alignment.CenterVertically) {
                IconButton(
                    onClick = onDelete,
                    modifier = Modifier.testTag("delete_rule_btn_${rule.id}")
                ) {
                    Icon(
                        imageVector = Icons.Default.Delete,
                        contentDescription = "删除规则",
                        tint = WarnRed.copy(alpha = 0.8f)
                    )
                }

                Switch(
                    checked = rule.isEnabled,
                    onCheckedChange = { onToggle() },
                    colors = SwitchDefaults.colors(
                        checkedThumbColor = Color.White,
                        checkedTrackColor = BoldNavy,
                        uncheckedThumbColor = BoldSteel,
                        uncheckedTrackColor = BoldSoftGrey
                    ),
                    modifier = Modifier.testTag("toggle_rule_${rule.id}")
                )
            }
        }
    }
}

fun mapActionType(type: String): String {
    return when (type) {
        "CLICK" -> "👆 点击"
        "LONG_CLICK" -> "👇 长按"
        "INPUT_TEXT" -> "✏️ 输入文本"
        "SCROLL_FORWARD" -> "⬆️ 向前/下滚动"
        "SCROLL_BACKWARD" -> "⬇️ 向后/上滚动"
        else -> type
    }
}

@Composable
fun LogsTab(
    liveLogs: List<String>,
    onClearLogs: () -> Unit
) {
    Column(
        modifier = Modifier
            .fillMaxSize()
            .padding(16.dp)
    ) {
        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.CenterVertically
        ) {
            Text(
                text = "高频引擎执行日志",
                color = BoldNavy,
                style = MaterialTheme.typography.titleMedium.copy(fontWeight = FontWeight.Black)
            )

            Button(
                onClick = onClearLogs,
                colors = ButtonDefaults.buttonColors(containerColor = BoldNavy, contentColor = Color.White),
                contentPadding = PaddingValues(horizontal = 14.dp, vertical = 4.dp),
                shape = RoundedCornerShape(12.dp),
                modifier = Modifier.testTag("clear_logs_btn")
            ) {
                Icon(Icons.Default.Delete, contentDescription = null, modifier = Modifier.size(16.dp))
                Spacer(modifier = Modifier.width(6.dp))
                Text("清空", style = MaterialTheme.typography.labelSmall.copy(fontWeight = FontWeight.Black))
            }
        }

        Spacer(modifier = Modifier.height(14.dp))

        Box(
            modifier = Modifier
                .fillMaxWidth()
                .weight(1f)
                .background(BoldNavy, RoundedCornerShape(24.dp))
                .border(2.dp, BoldIceBlue, RoundedCornerShape(24.dp))
                .padding(16.dp)
        ) {
            if (liveLogs.isEmpty()) {
                Box(modifier = Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                    Text(
                        text = "暂无秒级执行日志\n规则触发后将高速生成硬件轨迹日志",
                        color = BoldIceBlue.copy(alpha = 0.5f),
                        style = MaterialTheme.typography.bodyMedium.copy(fontWeight = FontWeight.Bold),
                        textAlign = TextAlign.Center
                    )
                }
            } else {
                LazyColumn(
                    modifier = Modifier.fillMaxSize(),
                    reverseLayout = false
                ) {
                    items(liveLogs) { log ->
                        Text(
                            text = log,
                            color = if (log.contains("成功") || log.contains("完成")) ActiveGreen 
                                    else if (log.contains("失败") || log.contains("被中断")) BoldSalmon
                                    else Color.White,
                            fontFamily = FontFamily.Monospace,
                            fontSize = 11.sp,
                            lineHeight = 16.sp,
                            modifier = Modifier
                                .fillMaxWidth()
                                .padding(vertical = 3.dp)
                        )
                    }
                }
            }
        }
    }
}

@Composable
fun EngineSettingsTab(
    currentInterval: Long,
    onSelectInterval: (Long) -> Unit
) {
    Column(
        modifier = Modifier
            .fillMaxSize()
            .padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(16.dp)
    ) {
        Text(
            text = "极速插值设置 Mode Support",
            color = BoldNavy,
            style = MaterialTheme.typography.titleMedium.copy(fontWeight = FontWeight.Black)
        )

        Card(
            modifier = Modifier.fillMaxWidth(),
            colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface),
            shape = RoundedCornerShape(24.dp),
            border = BorderStroke(2.dp, BoldIceBlue)
        ) {
            Column(
                modifier = Modifier.padding(16.dp),
                verticalArrangement = Arrangement.spacedBy(12.dp)
            ) {
                Text(
                    text = "手势拦截与扫描周期",
                    color = BoldNavy,
                    style = MaterialTheme.typography.bodyMedium.copy(fontWeight = FontWeight.Black)
                )

                listOf(50L, 100L, 250L, 500L).forEach { interval ->
                    Row(
                        modifier = Modifier
                            .fillMaxWidth()
                            .clip(RoundedCornerShape(14.dp))
                            .background(if (currentInterval == interval) BoldIceBlue else Color.Transparent)
                            .clickable { onSelectInterval(interval) }
                            .padding(12.dp),
                        verticalAlignment = Alignment.CenterVertically,
                        horizontalArrangement = Arrangement.SpaceBetween
                    ) {
                        Column(modifier = Modifier.weight(1f)) {
                            Text(
                                text = "${interval} ms / 扫描帧",
                                color = BoldNavy,
                                style = MaterialTheme.typography.bodyLarge.copy(fontWeight = FontWeight.Black)
                            )
                            Spacer(modifier = Modifier.height(2.dp))
                            Text(
                                text = when (interval) {
                                    50L -> "🔥 120Hz 极速模式：适用于瞬时抢购、辅助跳包，电池消耗中等"
                                    100L -> "⚡ 推荐极速模式：适用于高帧率流转、极速翻页阅读"
                                    250L -> "🌿 智能均衡模式：适合大多数自动日常连打、能效极佳"
                                    500L -> "🔋 环保低配模式：省电安静，适用于长链路后台挂载匹配"
                                    else -> ""
                                },
                                color = BoldSteel,
                                style = MaterialTheme.typography.labelSmall.copy(fontWeight = FontWeight.Bold)
                            )
                        }
                        RadioButton(
                            selected = currentInterval == interval,
                            onClick = { onSelectInterval(interval) },
                            colors = RadioButtonDefaults.colors(
                                selectedColor = BoldNavy,
                                unselectedColor = BoldSteel
                            )
                        )
                    }
                }
            }
        }

        Card(
            modifier = Modifier.fillMaxWidth(),
            colors = CardDefaults.cardColors(containerColor = BoldNavy),
            shape = RoundedCornerShape(24.dp),
            border = BorderStroke(2.dp, BoldIceBlue)
        ) {
            Column(modifier = Modifier.padding(20.dp)) {
                Text(
                    text = "高性能 120Hz 采样申明 (API 36)",
                    color = BoldSalmon,
                    style = MaterialTheme.typography.bodyMedium.copy(fontWeight = FontWeight.Black)
                )
                Spacer(modifier = Modifier.height(8.dp))
                Text(
                    text = "得益于 Android 16 的现代手势总线规范，该助手免去了旧版本多层调度导致的卡顿。本引擎在 120Hz 高刷新率屏幕上渲染点击轨迹最高可匹配其帧率。使用 50ms 超短微时扫码插值和 40ms 高纯触摸信号模拟流畅的物理手势，带来毫无突冗的高阻尼平滑模拟器体验。",
                    color = Color.White,
                    style = MaterialTheme.typography.bodySmall.copy(lineHeight = 18.sp)
                )
            }
        }
    }
}

@Composable
fun AddRuleDialog(
    onDismiss: () -> Unit,
    onConfirm: (AutomationRule) -> Unit
) {
    var name by remember { mutableStateOf("") }
    var targetType by remember { mutableStateOf("TEXT") } // "TEXT" or "ID"
    var targetValue by remember { mutableStateOf("") }
    var actionType by remember { mutableStateOf("CLICK") } // CLICK, LONG_CLICK, INPUT_TEXT, SCROLL_FORWARD, SCROLL_BACKWARD
    var inputValue by remember { mutableStateOf("") }
    var delayStr by remember { mutableStateOf("0") }

    Dialog(onDismissRequest = onDismiss) {
        Card(
            modifier = Modifier
                .fillMaxWidth()
                .padding(8.dp)
                .testTag("add_rule_dialog_card"),
            colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface),
            shape = RoundedCornerShape(32.dp),
            border = BorderStroke(2.dp, BoldIceBlue),
            elevation = CardDefaults.cardElevation(defaultElevation = 8.dp)
        ) {
            Column(
                modifier = Modifier
                    .padding(24.dp)
                    .fillMaxWidth(),
                verticalArrangement = Arrangement.spacedBy(16.dp)
            ) {
                Text(
                    text = "创建全新巡航规则",
                    color = BoldNavy,
                    style = MaterialTheme.typography.titleLarge.copy(fontWeight = FontWeight.Black)
                )

                OutlinedTextField(
                    value = name,
                    onValueChange = { name = it },
                    label = { Text("规则名称 (例如：跳过自动广告)") },
                    colors = OutlinedTextFieldDefaults.colors(
                        focusedBorderColor = BoldNavy,
                        focusedLabelColor = BoldNavy,
                        unfocusedBorderColor = BoldIceBlue,
                        focusedTextColor = BoldNavy,
                        unfocusedTextColor = BoldNavy,
                        unfocusedLabelColor = BoldSteel
                    ),
                    modifier = Modifier.fillMaxWidth().testTag("add_rule_name_field")
                )

                // Match Target Type Selector
                Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
                    Text("检测元素标准:", color = BoldSteel, style = MaterialTheme.typography.labelMedium.copy(fontWeight = FontWeight.Bold))
                    Row(
                        modifier = Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.spacedBy(10.dp)
                    ) {
                        listOf("TEXT" to "屏幕文字 (TEXT)", "ID" to "元素标识 (VIEW ID)").forEach { (type, label) ->
                            Box(
                                modifier = Modifier
                                    .weight(1f)
                                    .clip(RoundedCornerShape(12.dp))
                                    .background(if (targetType == type) BoldIceBlue else Color.Transparent)
                                    .border(2.dp, if (targetType == type) BoldNavy else BoldIceBlue, RoundedCornerShape(12.dp))
                                    .clickable { targetType = type }
                                    .padding(vertical = 12.dp),
                                contentAlignment = Alignment.Center
                            ) {
                                Text(
                                    text = label,
                                    color = BoldNavy,
                                    fontSize = 12.sp,
                                    fontWeight = FontWeight.Black
                                )
                            }
                        }
                    }
                }

                OutlinedTextField(
                    value = targetValue,
                    onValueChange = { targetValue = it },
                    label = { 
                        Text(if (targetType == "TEXT") "匹配关键字(例如：跳过)" else "完整资源ID(例如：btn_skip)") 
                    },
                    colors = OutlinedTextFieldDefaults.colors(
                        focusedBorderColor = BoldNavy,
                        focusedLabelColor = BoldNavy,
                        unfocusedBorderColor = BoldIceBlue,
                        focusedTextColor = BoldNavy,
                        unfocusedTextColor = BoldNavy,
                        unfocusedLabelColor = BoldSteel
                    ),
                    modifier = Modifier.fillMaxWidth().testTag("add_rule_target_value_field")
                )

                // Simulated action Selector
                Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
                    Text("执行自动手势:", color = BoldSteel, style = MaterialTheme.typography.labelMedium.copy(fontWeight = FontWeight.Bold))
                    val actions = listOf("CLICK", "LONG_CLICK", "INPUT_TEXT", "SCROLL_FORWARD", "SCROLL_BACKWARD")
                    
                    Row(
                        modifier = Modifier
                            .fillMaxWidth()
                            .border(2.dp, BoldIceBlue, RoundedCornerShape(14.dp))
                            .padding(4.dp),
                        horizontalArrangement = Arrangement.SpaceBetween,
                        verticalAlignment = Alignment.CenterVertically
                    ) {
                        actions.forEach { action ->
                            Box(
                                modifier = Modifier
                                    .clip(RoundedCornerShape(10.dp))
                                    .background(if (actionType == action) BoldNavy else Color.Transparent)
                                    .clickable { actionType = action }
                                    .padding(horizontal = 8.dp, vertical = 8.dp)
                            ) {
                                Text(
                                    text = when (action) {
                                        "CLICK" -> "点击"
                                        "LONG_CLICK" -> "长按"
                                        "INPUT_TEXT" -> "输入"
                                        "SCROLL_FORWARD" -> "向后滚"
                                        "SCROLL_BACKWARD" -> "向前滚"
                                        else -> action
                                    },
                                    color = if (actionType == action) Color.White else BoldSteel,
                                    fontSize = 11.sp,
                                    fontWeight = FontWeight.Black
                                )
                            }
                        }
                    }
                }

                if (actionType == "INPUT_TEXT") {
                    OutlinedTextField(
                        value = inputValue,
                        onValueChange = { inputValue = it },
                        label = { Text("自动输入填充文本") },
                        colors = OutlinedTextFieldDefaults.colors(
                            focusedBorderColor = BoldNavy,
                            focusedLabelColor = BoldNavy,
                            unfocusedBorderColor = BoldIceBlue,
                            focusedTextColor = BoldNavy,
                            unfocusedTextColor = BoldNavy,
                            unfocusedLabelColor = BoldSteel
                        ),
                        modifier = Modifier.fillMaxWidth().testTag("add_rule_input_value_field")
                    )
                }

                OutlinedTextField(
                    value = delayStr,
                    onValueChange = { delayStr = it.filter { char -> char.isDigit() } },
                    label = { Text("执行前延时(毫秒, 推荐0-1000)") },
                    keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number),
                    colors = OutlinedTextFieldDefaults.colors(
                        focusedBorderColor = BoldNavy,
                        focusedLabelColor = BoldNavy,
                        unfocusedBorderColor = BoldIceBlue,
                        focusedTextColor = BoldNavy,
                        unfocusedTextColor = BoldNavy,
                        unfocusedLabelColor = BoldSteel
                    ),
                    modifier = Modifier.fillMaxWidth().testTag("add_rule_delay_field")
                )

                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.End,
                    verticalAlignment = Alignment.CenterVertically
                ) {
                    TextButton(onClick = onDismiss, modifier = Modifier.testTag("dialog_cancel")) {
                        Text("取消", color = BoldSteel, fontWeight = FontWeight.Bold)
                    }
                    
                    Spacer(modifier = Modifier.width(12.dp))

                    Button(
                        onClick = {
                            if (name.isNotEmpty() && targetValue.isNotEmpty()) {
                                onConfirm(
                                    AutomationRule(
                                        name = name,
                                        targetType = targetType,
                                        targetValue = targetValue,
                                        actionType = actionType,
                                        inputValue = inputValue,
                                        delayMs = delayStr.toLongOrNull() ?: 0L
                                    )
                                )
                            }
                        },
                        enabled = name.isNotEmpty() && targetValue.isNotEmpty(),
                        colors = ButtonDefaults.buttonColors(
                            containerColor = BoldNavy, 
                            contentColor = Color.White,
                            disabledContainerColor = BoldSoftGrey,
                            disabledContentColor = BoldSteel
                        ),
                        shape = RoundedCornerShape(12.dp),
                        modifier = Modifier.testTag("dialog_submit")
                    ) {
                        Text("确认创建", fontWeight = FontWeight.Black)
                    }
                }
            }
        }
    }
}
