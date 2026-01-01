/**
 * Taurus Signal - AutoX.js 信号接收脚本
 * 
 * 功能：
 * 1. 连接 WebSocket 接收实时信号
 * 2. 信号到达时震动+通知提醒
 * 3. 显示悬浮窗实时状态
 * 4. 自动重连机制
 */

"ui";

// ==================== 配置 ====================
var CONFIG = {
    // 信号服务地址（改成你的服务器地址）
    WS_URL: "ws://192.168.1.100:8000/ws",
    API_URL: "http://192.168.1.100:8000",
    
    // 只接收这些等级的信号 (S/A/B/C)
    ACCEPT_LEVELS: ["S", "A"],
    
    // 提醒设置
    VIBRATE: true,          // 震动提醒
    NOTIFICATION: true,     // 系统通知
    SOUND: true,            // 声音提醒
    
    // 重连设置
    RECONNECT_DELAY: 3000,  // 重连延迟(ms)
    MAX_RECONNECT: 100,     // 最大重连次数
};

// ==================== 全局变量 ====================
var ws = null;
var isConnected = false;
var reconnectCount = 0;
var signalCount = 0;
var lastSignal = null;

// ==================== UI 界面 ====================
ui.layout(
    <vertical padding="16">
        <text text="Taurus Signal" textSize="24sp" textColor="#3b82f6" gravity="center"/>
        <text text="币安事件合约信号接收器" textSize="12sp" textColor="#666" gravity="center" marginBottom="16"/>
        
        <horizontal>
            <text text="服务器: " textColor="#333"/>
            <input id="serverUrl" text="ws://192.168.1.100:8000/ws" layout_weight="1" textSize="12sp"/>
        </horizontal>
        
        <horizontal marginTop="8">
            <text text="接收等级: " textColor="#333"/>
            <checkbox id="levelS" text="S" checked="true"/>
            <checkbox id="levelA" text="A" checked="true"/>
            <checkbox id="levelB" text="B" checked="false"/>
            <checkbox id="levelC" text="C" checked="false"/>
        </horizontal>
        
        <horizontal marginTop="16">
            <button id="btnConnect" text="连接" layout_weight="1" style="Widget.AppCompat.Button.Colored"/>
            <button id="btnDisconnect" text="断开" layout_weight="1" marginLeft="8"/>
        </horizontal>
        
        <card marginTop="16" cardCornerRadius="8dp" cardElevation="2dp">
            <vertical padding="12">
                <horizontal>
                    <text text="状态: " textColor="#666"/>
                    <text id="txtStatus" text="未连接" textColor="#ef4444"/>
                </horizontal>
                <horizontal marginTop="4">
                    <text text="信号数: " textColor="#666"/>
                    <text id="txtSignalCount" text="0" textColor="#333"/>
                </horizontal>
            </vertical>
        </card>
        
        <text text="最新信号" textSize="14sp" textColor="#333" marginTop="16"/>
        <card marginTop="8" cardCornerRadius="8dp" cardElevation="2dp">
            <vertical id="signalCard" padding="12">
                <text id="txtLastSignal" text="等待信号..." textColor="#666"/>
            </vertical>
        </card>
        
        <text text="日志" textSize="14sp" textColor="#333" marginTop="16"/>
        <scroll layout_weight="1" marginTop="8">
            <text id="txtLog" text="" textSize="11sp" textColor="#666"/>
        </scroll>
    </vertical>
);

// ==================== 事件处理 ====================
ui.btnConnect.on("click", function() {
    CONFIG.WS_URL = ui.serverUrl.getText().toString();
    updateAcceptLevels();
    connect();
});

ui.btnDisconnect.on("click", function() {
    disconnect();
});

// ==================== 核心函数 ====================
function updateAcceptLevels() {
    CONFIG.ACCEPT_LEVELS = [];
    if (ui.levelS.isChecked()) CONFIG.ACCEPT_LEVELS.push("S");
    if (ui.levelA.isChecked()) CONFIG.ACCEPT_LEVELS.push("A");
    if (ui.levelB.isChecked()) CONFIG.ACCEPT_LEVELS.push("B");
    if (ui.levelC.isChecked()) CONFIG.ACCEPT_LEVELS.push("C");
}

function connect() {
    if (isConnected) {
        log("已经连接");
        return;
    }
    
    log("正在连接: " + CONFIG.WS_URL);
    updateStatus("连接中...", "#f59e0b");
    
    try {
        ws = new WebSocket(CONFIG.WS_URL);
        
        ws.on("open", function() {
            isConnected = true;
            reconnectCount = 0;
            log("✅ 连接成功");
            updateStatus("已连接", "#22c55e");
        });
        
        ws.on("message", function(msg) {
            handleMessage(msg);
        });
        
        ws.on("close", function() {
            isConnected = false;
            log("连接断开");
            updateStatus("已断开", "#ef4444");
            scheduleReconnect();
        });
        
        ws.on("error", function(err) {
            log("❌ 连接错误: " + err);
            updateStatus("错误", "#ef4444");
        });
        
    } catch (e) {
        log("❌ 连接失败: " + e);
        updateStatus("连接失败", "#ef4444");
        scheduleReconnect();
    }
}

function disconnect() {
    reconnectCount = CONFIG.MAX_RECONNECT; // 阻止自动重连
    if (ws) {
        ws.close();
        ws = null;
    }
    isConnected = false;
    log("已断开连接");
    updateStatus("已断开", "#ef4444");
}

function scheduleReconnect() {
    if (reconnectCount >= CONFIG.MAX_RECONNECT) {
        log("达到最大重连次数，停止重连");
        return;
    }
    
    reconnectCount++;
    log("将在 " + (CONFIG.RECONNECT_DELAY / 1000) + " 秒后重连 (" + reconnectCount + "/" + CONFIG.MAX_RECONNECT + ")");
    
    setTimeout(function() {
        if (!isConnected) {
            connect();
        }
    }, CONFIG.RECONNECT_DELAY);
}

function handleMessage(msg) {
    try {
        var data = JSON.parse(msg);
        
        if (data.type === "signal") {
            handleSignal(data.data);
        } else if (data.type === "settlement") {
            handleSettlement(data.data);
        }
        
    } catch (e) {
        log("解析消息失败: " + e);
    }
}

function handleSignal(signal) {
    // 检查是否接收该等级
    if (CONFIG.ACCEPT_LEVELS.indexOf(signal.level) === -1) {
        log("忽略 " + signal.level + " 级信号");
        return;
    }
    
    signalCount++;
    lastSignal = signal;
    
    var direction = signal.direction === "UP" ? "做多" : "做空";
    var msg = signal.symbol + " " + direction + " " + signal.level + "级 置信度:" + (signal.confidence * 100).toFixed(1) + "%";
    
    log("🔔 新信号: " + msg);
    
    // 更新UI
    ui.run(function() {
        ui.txtSignalCount.setText(signalCount.toString());
        ui.txtLastSignal.setText(
            "交易对: " + signal.symbol + "\n" +
            "方向: " + direction + "\n" +
            "等级: " + signal.level + "\n" +
            "置信度: " + (signal.confidence * 100).toFixed(1) + "%\n" +
            "入场价: $" + signal.entry_price.toLocaleString() + "\n" +
            "下注: " + signal.bet_amount + "U\n" +
            "时间: " + new Date().toLocaleTimeString()
        );
        
        // 根据方向设置背景色
        if (signal.direction === "UP") {
            ui.signalCard.attr("cardBackgroundColor", "#dcfce7");
        } else {
            ui.signalCard.attr("cardBackgroundColor", "#fee2e2");
        }
    });
    
    // 提醒
    if (CONFIG.VIBRATE) {
        device.vibrate(500);
    }
    
    if (CONFIG.NOTIFICATION) {
        notification(signal);
    }
    
    if (CONFIG.SOUND) {
        media.playMusic("/system/media/audio/notifications/OnTheHunt.ogg");
    }
    
    // TODO: 这里可以调用自动下单函数
    // placeOrder(signal);
}

function handleSettlement(settlement) {
    var result = settlement.is_win ? "✅ 盈利" : "❌ 亏损";
    var pnl = settlement.pnl > 0 ? "+" + settlement.pnl.toFixed(1) : settlement.pnl.toFixed(1);
    log("📊 结算: " + settlement.symbol + " " + result + " " + pnl + "U");
}

function notification(signal) {
    var direction = signal.direction === "UP" ? "做多" : "做空";
    var title = "Taurus Signal - " + signal.level + "级信号";
    var content = signal.symbol + " " + direction + " 置信度:" + (signal.confidence * 100).toFixed(1) + "%";
    
    notice(title, content);
}

function notice(title, content) {
    var builder = new android.app.Notification.Builder(context)
        .setContentTitle(title)
        .setContentText(content)
        .setSmallIcon(android.R.drawable.ic_dialog_info)
        .setAutoCancel(true);
    
    if (android.os.Build.VERSION.SDK_INT >= 26) {
        var channel = new android.app.NotificationChannel(
            "taurus_signal",
            "Taurus Signal",
            android.app.NotificationManager.IMPORTANCE_HIGH
        );
        context.getSystemService(android.app.NotificationManager).createNotificationChannel(channel);
        builder.setChannelId("taurus_signal");
    }
    
    context.getSystemService(android.app.NotificationManager).notify(1, builder.build());
}

function updateStatus(text, color) {
    ui.run(function() {
        ui.txtStatus.setText(text);
        ui.txtStatus.setTextColor(colors.parseColor(color));
    });
}

function log(msg) {
    var time = new Date().toLocaleTimeString();
    var logText = "[" + time + "] " + msg;
    console.log(logText);
    
    ui.run(function() {
        var current = ui.txtLog.getText().toString();
        var lines = current.split("\n");
        if (lines.length > 100) {
            lines = lines.slice(-50);
        }
        lines.push(logText);
        ui.txtLog.setText(lines.join("\n"));
    });
}

// ==================== 启动 ====================
log("Taurus Signal 启动");
log("接收等级: " + CONFIG.ACCEPT_LEVELS.join(", "));

// 开启前台服务保活
var foreground = require("foreground");
foreground.start({
    title: "Taurus Signal",
    text: "信号监听中...",
    bigText: "正在监听币安事件合约信号"
});

// 保持屏幕常亮（可选，比较耗电）
// device.keepScreenOn();

// 脚本退出时清理
events.on("exit", function() {
    log("脚本退出");
    if (ws) {
        ws.close();
    }
    foreground.stop();
});
