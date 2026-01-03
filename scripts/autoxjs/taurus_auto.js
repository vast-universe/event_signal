/**
 * Taurus Signal - 后台自动下单脚本（无UI界面）
 * 运行后自动连接服务器，收到信号自动下单
 */

// ==================== 配置 ====================
var CONFIG = {
    WS_URL: "ws://192.168.31.15:8000/ws",
    ACCEPT_LEVELS: ["S", "A", "B", "C"],
    ACCEPT_SYMBOLS: "ALL",  // "ALL" / "BTCUSDT" / "ETHUSDT"
    AUTO_TRADE: true,
    TRADE_DELAY: 300,
    VIBRATE: false,
};

// ==================== 全局变量 ====================
var ws = null;
var isConnected = false;
var reconnectCount = 0;
var signalCount = 0;
var floatyWindow = null;
var currentSymbol = "BTCUSDT";  // 当前币对
var tradeQueue = [];  // 下单队列
var isTrading = false;  // 是否正在下单

// ==================== 悬浮窗 ====================
function createFloaty() {
    floatyWindow = floaty.rawWindow(
        <vertical bg="#cc000000" padding="12" id="container">
            <text id="status" text="连接中..." textColor="#ffffff" textSize="12sp"/>
            <text id="signal" text="" textColor="#ffff00" textSize="11sp" marginTop="4"/>
            <horizontal marginTop="6">
                <button id="btnAll" text="全部" textSize="10sp" w="45" h="30" bg="#2196f3"/>
                <button id="btnBtc" text="BTC" textSize="10sp" w="45" h="30" bg="#666666" marginLeft="4"/>
                <button id="btnEth" text="ETH" textSize="10sp" w="45" h="30" bg="#666666" marginLeft="4"/>
            </horizontal>
            <horizontal marginTop="4">
                <button id="btnS" text="S" textSize="10sp" w="32" h="28" bg="#9c27b0"/>
                <button id="btnA" text="A" textSize="10sp" w="32" h="28" bg="#2196f3" marginLeft="3"/>
                <button id="btnB" text="B" textSize="10sp" w="32" h="28" bg="#4caf50" marginLeft="3"/>
                <button id="btnC" text="C" textSize="10sp" w="32" h="28" bg="#ff9800" marginLeft="3"/>
            </horizontal>
            <button id="btnConn" text="断开" textSize="10sp" w="*" h="30" bg="#f44336" marginTop="4"/>
        </vertical>
    );
    floatyWindow.setPosition(device.width - 180, 200);
    
    // 币对选择按钮
    floatyWindow.btnAll.on("click", function() {
        CONFIG.ACCEPT_SYMBOLS = "ALL";
        updateSymbolButtons();
        log("切换: 全部币对");
    });
    floatyWindow.btnBtc.on("click", function() {
        CONFIG.ACCEPT_SYMBOLS = "BTCUSDT";
        updateSymbolButtons();
        log("切换: 仅BTC");
    });
    floatyWindow.btnEth.on("click", function() {
        CONFIG.ACCEPT_SYMBOLS = "ETHUSDT";
        updateSymbolButtons();
        log("切换: 仅ETH");
    });
    
    // 等级选择按钮
    floatyWindow.btnS.on("click", function() { toggleLevel("S"); });
    floatyWindow.btnA.on("click", function() { toggleLevel("A"); });
    floatyWindow.btnB.on("click", function() { toggleLevel("B"); });
    floatyWindow.btnC.on("click", function() { toggleLevel("C"); });
    
    // 连接/断开按钮
    floatyWindow.btnConn.on("click", function() {
        if (isConnected) {
            disconnect();
        } else {
            connect();
        }
    });
    
    // 拖动功能
    var x = 0, y = 0;
    var windowX = 0, windowY = 0;
    var downTime = 0;
    
    floatyWindow.container.setOnTouchListener(function(view, event) {
        switch (event.getAction()) {
            case event.ACTION_DOWN:
                x = event.getRawX();
                y = event.getRawY();
                windowX = floatyWindow.getX();
                windowY = floatyWindow.getY();
                downTime = new Date().getTime();
                return true;
            case event.ACTION_MOVE:
                floatyWindow.setPosition(windowX + (event.getRawX() - x), windowY + (event.getRawY() - y));
                return true;
            case event.ACTION_UP:
                if (new Date().getTime() - downTime < 150) {
                    return false;
                }
                return true;
        }
        return true;
    });
}

function toggleLevel(level) {
    var idx = CONFIG.ACCEPT_LEVELS.indexOf(level);
    if (idx >= 0) {
        CONFIG.ACCEPT_LEVELS.splice(idx, 1);
    } else {
        CONFIG.ACCEPT_LEVELS.push(level);
    }
    updateLevelButtons();
    log("接收等级: " + CONFIG.ACCEPT_LEVELS.join(","));
}

function updateSymbolButtons() {
    ui.run(function() {
        floatyWindow.btnAll.setBackgroundColor(CONFIG.ACCEPT_SYMBOLS === "ALL" ? colors.parseColor("#2196f3") : colors.parseColor("#666666"));
        floatyWindow.btnBtc.setBackgroundColor(CONFIG.ACCEPT_SYMBOLS === "BTCUSDT" ? colors.parseColor("#2196f3") : colors.parseColor("#666666"));
        floatyWindow.btnEth.setBackgroundColor(CONFIG.ACCEPT_SYMBOLS === "ETHUSDT" ? colors.parseColor("#2196f3") : colors.parseColor("#666666"));
    });
}

function updateLevelButtons() {
    ui.run(function() {
        var hasS = CONFIG.ACCEPT_LEVELS.indexOf("S") >= 0;
        var hasA = CONFIG.ACCEPT_LEVELS.indexOf("A") >= 0;
        var hasB = CONFIG.ACCEPT_LEVELS.indexOf("B") >= 0;
        var hasC = CONFIG.ACCEPT_LEVELS.indexOf("C") >= 0;
        floatyWindow.btnS.setBackgroundColor(hasS ? colors.parseColor("#9c27b0") : colors.parseColor("#333333"));
        floatyWindow.btnA.setBackgroundColor(hasA ? colors.parseColor("#2196f3") : colors.parseColor("#333333"));
        floatyWindow.btnB.setBackgroundColor(hasB ? colors.parseColor("#4caf50") : colors.parseColor("#333333"));
        floatyWindow.btnC.setBackgroundColor(hasC ? colors.parseColor("#ff9800") : colors.parseColor("#333333"));
    });
}

function updateConnButton() {
    ui.run(function() {
        if (isConnected) {
            floatyWindow.btnConn.setText("断开");
            floatyWindow.btnConn.setBackgroundColor(colors.parseColor("#f44336"));
        } else {
            floatyWindow.btnConn.setText("连接");
            floatyWindow.btnConn.setBackgroundColor(colors.parseColor("#4caf50"));
        }
    });
}

function disconnect() {
    reconnectCount = 100;  // 阻止自动重连
    if (ws) {
        ws.close(1000, "用户断开");
        ws = null;
    }
    isConnected = false;
    log("已断开连接");
    updateFloaty("✗ 已断开", "");
    updateConnButton();
}

function updateFloaty(status, signal) {
    if (floatyWindow) {
        ui.run(function() {
            if (status) floatyWindow.status.setText(status);
            if (signal) floatyWindow.signal.setText(signal);
        });
    }
}

// ==================== WebSocket ====================
function connect() {
    if (isConnected) return;
    
    log("连接: " + CONFIG.WS_URL);
    updateFloaty("连接中...", "");
    
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
                log("已连接");
                updateFloaty("✓ 已连接 等待信号", "");
                updateConnButton();
            },
            onMessage: function(webSocket, text) {
                handleMessage(text);
            },
            onClosed: function(webSocket, code, reason) {
                isConnected = false;
                log("断开");
                updateFloaty("✗ 已断开", "");
                scheduleReconnect();
            },
            onFailure: function(webSocket, t, response) {
                isConnected = false;
                log("错误: " + t.getMessage());
                updateFloaty("✗ 连接错误", "");
                scheduleReconnect();
            }
        });
        
        ws = client.newWebSocket(request, listener);
    } catch (e) {
        log("连接失败: " + e);
        scheduleReconnect();
    }
}

function scheduleReconnect() {
    if (reconnectCount >= 100) return;
    reconnectCount++;
    setTimeout(function() {
        if (!isConnected) connect();
    }, 3000);
}

function handleMessage(msg) {
    try {
        var data = JSON.parse(msg);
        if (data.type === "signal") {
            handleSignal(data.data);
        }
    } catch (e) {
        log("解析失败: " + e);
    }
}

function handleSignal(signal) {
    if (CONFIG.ACCEPT_LEVELS.indexOf(signal.level) === -1) {
        return;
    }
    
    // 币对过滤
    if (CONFIG.ACCEPT_SYMBOLS !== "ALL" && signal.symbol !== CONFIG.ACCEPT_SYMBOLS) {
        log("忽略币对: " + signal.symbol);
        return;
    }
    
    signalCount++;
    var dir = signal.direction === "UP" ? "做多" : "做空";
    var msg = signal.symbol + " " + dir + " " + signal.level + "级 " + signal.bet_amount + "U";
    
    log("信号: " + msg);
    updateFloaty("✓ 已连接 #" + signalCount, msg);
    
    // 自动下单 - 加入队列
    if (CONFIG.AUTO_TRADE) {
        // 唤醒屏幕
        if (!device.isScreenOn()) {
            device.wakeUp();
            sleep(500);
        }
        addToQueue(signal.symbol, signal.direction, signal.bet_amount);
    }
}

// 加入下单队列
function addToQueue(symbol, direction, amount) {
    tradeQueue.push({ symbol: symbol, direction: direction, amount: amount });
    log("加入队列: " + symbol + " " + direction + " " + amount + "U (队列长度: " + tradeQueue.length + ")");
    processQueue();
}

// 处理队列
function processQueue() {
    if (isTrading || tradeQueue.length === 0) {
        return;
    }
    
    isTrading = true;
    var task = tradeQueue.shift();
    
    threads.start(function() {
        try {
            executeTrade(task.symbol, task.direction, task.amount);
        } finally {
            isTrading = false;
            // 处理下一个
            if (tradeQueue.length > 0) {
                sleep(500);
                processQueue();
            }
        }
    });
}

// ==================== 自动下单 ====================
function executeTrade(symbol, direction, amount) {
    try {
        log("下单: " + symbol + " " + direction + " " + amount + "U");
        
        // 切换币对（如果需要）
        if (symbol !== currentSymbol) {
            if (switchSymbol(symbol)) {
                currentSymbol = symbol;
                sleep(500);
            } else {
                log("切换币对失败");
                updateFloaty("✗ 切换币对失败", symbol);
                return;
            }
        }
        
        // 滚动确保按钮可见
        swipe(device.width / 2, device.height * 0.6, device.width / 2, device.height * 0.4, 200);
        sleep(CONFIG.TRADE_DELAY);
        
        // 输入金额
        var input = className("android.widget.EditText").findOne(800);
        if (input) {
            input.click();
            sleep(150);
            input.setText(amount.toString());
            sleep(CONFIG.TRADE_DELAY);
            
            // 关闭键盘
            back();
            sleep(300);
        }
        
        // 点击按钮
        var success = false;
        if (direction === "UP") {
            success = clickButton("上涨") || clickButton("涨");
        } else {
            success = clickButton("下跌") || clickButton("跌");
        }
        
        // 点击确认弹窗
        if (success) {
            sleep(300);
            clickButton("确认") || clickButton("确定");
            sleep(200);
        }
        
        if (success) {
            log("下单成功");
            updateFloaty("✓ 下单成功", symbol + " " + (direction === "UP" ? "做多" : "做空") + " " + amount + "U");
        } else {
            log("下单失败: 未找到按钮");
            updateFloaty("✗ 下单失败", "未找到按钮");
        }
    } catch (e) {
        log("下单异常: " + e);
    }
}

// 切换币对 - 点击后底部弹窗选择
function switchSymbol(symbol) {
    log("切换币对: " + symbol);
    
    // 1. 点击当前币对触发底部弹窗
    var symbolBtn = textContains("BTC").findOne(500) || textContains("ETH").findOne(500);
    if (!symbolBtn) {
        symbolBtn = descContains("BTC").findOne(500) || descContains("ETH").findOne(500);
    }
    
    if (symbolBtn) {
        var bounds = symbolBtn.bounds();
        click(bounds.centerX(), bounds.centerY());
        sleep(500);  // 等待弹窗出现
        
        // 2. 在底部弹窗中点击目标币对
        var targetText = symbol.replace("USDT", "");  // BTCUSDT -> BTC
        var target = text(targetText).findOne(800) || textContains(targetText).findOne(800);
        if (target) {
            var targetBounds = target.bounds();
            click(targetBounds.centerX(), targetBounds.centerY());
            sleep(300);
            log("切换成功: " + symbol);
            return true;
        } else {
            log("未找到目标币对: " + targetText);
            // 点击空白处关闭弹窗
            click(device.width / 2, device.height * 0.3);
        }
    } else {
        log("未找到币对选择器");
    }
    
    return false;
}

function clickButton(btnText) {
    // 方式1: text 精确匹配
    var btn = text(btnText).findOne(500);
    if (btn) {
        log("找到按钮(text): " + btnText);
        var bounds = btn.bounds();
        click(bounds.centerX(), bounds.centerY());
        return true;
    }
    
    // 方式2: textContains
    btn = textContains(btnText).findOne(500);
    if (btn) {
        log("找到按钮(textContains): " + btnText);
        var bounds = btn.bounds();
        click(bounds.centerX(), bounds.centerY());
        return true;
    }
    
    // 方式3: desc
    btn = desc(btnText).findOne(300);
    if (btn) {
        log("找到按钮(desc): " + btnText);
        var bounds = btn.bounds();
        click(bounds.centerX(), bounds.centerY());
        return true;
    }
    
    // 方式4: descContains
    btn = descContains(btnText).findOne(300);
    if (btn) {
        log("找到按钮(descContains): " + btnText);
        var bounds = btn.bounds();
        click(bounds.centerX(), bounds.centerY());
        return true;
    }
    
    log("未找到按钮: " + btnText);
    return false;
}

function log(msg) {
    console.log("[Taurus] " + msg);
}

// ==================== 启动 ====================
log("Taurus 自动下单启动");

// 检查无障碍
if (!auto.service) {
    toast("请先开启无障碍服务");
    auto.waitFor();
}

// 保持屏幕常亮
device.keepScreenOn();

// 创建悬浮窗
createFloaty();

// 连接服务器
connect();

// 保持运行
setInterval(function() {}, 1000);

events.on("exit", function() {
    if (floatyWindow) floatyWindow.close();
    if (ws) ws.close();
});