/**
 * Taurus Signal - AutoX.js 信号接收脚本
 */

"ui";

// ==================== 配置 ====================
var CONFIG = {
    WS_URL: "ws://192.168.31.15:8000/ws",
    API_URL: "http://192.168.31.15:8000",
    ACCEPT_LEVELS: ["S", "A", "B", "C"],
    VIBRATE: true,
    NOTIFICATION: true,
    SOUND: true,
    RECONNECT_DELAY: 3000,
    MAX_RECONNECT: 100,
};

// ==================== 全局变量 ====================
var ws = null;
var isConnected = false;
var reconnectCount = 0;
var signalCount = 0;

// ==================== UI 界面 ====================
ui.layout(
    <vertical padding="16" bg="#ffffff">
        <text text="Taurus Signal" textSize="24sp" textColor="@color/colorPrimary" gravity="center"/>
        <text text="币安事件合约信号接收器" textSize="12sp" textColor="#666666" gravity="center" marginBottom="16"/>
        
        <horizontal>
            <text text="服务器: " textColor="#333333"/>
            <input id="serverUrl" text="ws://192.168.31.15:8000/ws" layout_weight="1" textSize="12sp"/>
        </horizontal>
        
        <horizontal marginTop="8">
            <text text="接收等级: " textColor="#333333"/>
            <checkbox id="levelS" text="S" checked="true"/>
            <checkbox id="levelA" text="A" checked="true"/>
            <checkbox id="levelB" text="B" checked="true"/>
            <checkbox id="levelC" text="C" checked="true"/>
        </horizontal>
        
        <horizontal marginTop="16">
            <button id="btnConnect" text="连接" layout_weight="1" style="Widget.AppCompat.Button.Colored"/>
            <button id="btnDisconnect" text="断开" layout_weight="1" marginLeft="8"/>
        </horizontal>
        
        <card marginTop="16" cardCornerRadius="8dp" cardElevation="2dp">
            <vertical padding="12">
                <horizontal>
                    <text text="状态: " textColor="#666666"/>
                    <text id="txtStatus" text="未连接" textColor="#ff0000"/>
                </horizontal>
                <horizontal marginTop="4">
                    <text text="信号数: " textColor="#666666"/>
                    <text id="txtSignalCount" text="0" textColor="#333333"/>
                </horizontal>
            </vertical>
        </card>
        
        <text text="最新信号" textSize="14sp" textColor="#333333" marginTop="16"/>
        <card marginTop="8" cardCornerRadius="8dp" cardElevation="2dp" id="signalCard">
            <vertical padding="12">
                <text id="txtLastSignal" text="等待信号..." textColor="#666666"/>
            </vertical>
        </card>
        
        <text text="日志" textSize="14sp" textColor="#333333" marginTop="16"/>
        <scroll layout_weight="1" marginTop="8">
            <text id="txtLog" text="" textSize="11sp" textColor="#666666"/>
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
    updateStatus("连接中...", "#ff9800");
    
    try {
        var okhttp3 = Packages.okhttp3;
        var client = new okhttp3.OkHttpClient.Builder()
            .retryOnConnectionFailure(true)
            .build();
        
        var request = new okhttp3.Request.Builder()
            .url(CONFIG.WS_URL)
            .build();
        
        var listener = new okhttp3.WebSocketListener({
            onOpen: function(webSocket, response) {
                isConnected = true;
                reconnectCount = 0;
                log("连接成功");
                updateStatus("已连接", "#4caf50");
            },
            onMessage: function(webSocket, text) {
                handleMessage(text);
            },
            onClosing: function(webSocket, code, reason) {
                webSocket.close(1000, null);
            },
            onClosed: function(webSocket, code, reason) {
                isConnected = false;
                log("连接断开");
                updateStatus("已断开", "#f44336");
                scheduleReconnect();
            },
            onFailure: function(webSocket, t, response) {
                isConnected = false;
                log("连接错误: " + t.getMessage());
                updateStatus("错误", "#f44336");
                scheduleReconnect();
            }
        });
        
        ws = client.newWebSocket(request, listener);
        
    } catch (e) {
        log("连接失败: " + e);
        updateStatus("连接失败", "#f44336");
        scheduleReconnect();
    }
}

function disconnect() {
    reconnectCount = CONFIG.MAX_RECONNECT;
    if (ws) {
        ws.close(1000, "用户断开");
        ws = null;
    }
    isConnected = false;
    log("已断开连接");
    updateStatus("已断开", "#f44336");
}

function scheduleReconnect() {
    if (reconnectCount >= CONFIG.MAX_RECONNECT) {
        log("达到最大重连次数");
        return;
    }
    
    reconnectCount++;
    log("将在3秒后重连 (" + reconnectCount + "/" + CONFIG.MAX_RECONNECT + ")");
    
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
    if (CONFIG.ACCEPT_LEVELS.indexOf(signal.level) === -1) {
        log("忽略 " + signal.level + " 级信号");
        return;
    }
    
    signalCount++;
    
    var direction = signal.direction === "UP" ? "做多" : "做空";
    var msg = signal.symbol + " " + direction + " " + signal.level + "级";
    
    log("新信号: " + msg);
    
    ui.run(function() {
        ui.txtSignalCount.setText(signalCount.toString());
        ui.txtLastSignal.setText(
            "交易对: " + signal.symbol + "\n" +
            "方向: " + direction + "\n" +
            "等级: " + signal.level + "\n" +
            "置信度: " + (signal.confidence * 100).toFixed(1) + "%\n" +
            "入场价: $" + signal.entry_price + "\n" +
            "下注: " + signal.bet_amount + "U\n" +
            "时间: " + new Date().toLocaleTimeString()
        );
    });
    
    if (CONFIG.VIBRATE) {
        device.vibrate(500);
    }
    
    if (CONFIG.NOTIFICATION) {
        var title = "Taurus - " + signal.level + "级信号";
        var content = signal.symbol + " " + direction;
        toast(title + ": " + content);
    }
}

function handleSettlement(settlement) {
    var result = settlement.is_win ? "盈利" : "亏损";
    var pnl = settlement.pnl > 0 ? "+" + settlement.pnl.toFixed(1) : settlement.pnl.toFixed(1);
    log("结算: " + settlement.symbol + " " + result + " " + pnl + "U");
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
        if (lines.length > 50) {
            lines = lines.slice(-30);
        }
        lines.push(logText);
        ui.txtLog.setText(lines.join("\n"));
    });
}

// ==================== 启动 ====================
log("Taurus Signal 启动");
log("接收等级: " + CONFIG.ACCEPT_LEVELS.join(", "));

events.on("exit", function() {
    log("脚本退出");
    if (ws) {
        ws.close();
    }
});
