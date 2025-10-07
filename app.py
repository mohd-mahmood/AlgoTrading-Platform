"""
Integrated AlgoTrading Platform - Fixed UI Version
Backend + Frontend in single deployment
"""

from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from flask_socketio import SocketIO, emit
import threading
import json
import pandas as pd
from datetime import datetime, timedelta
import importlib.util
import sys
import os
import webbrowser

# AngelOne imports
try:
    from SmartApi import SmartConnect
    from SmartApi.smartWebSocketV2 import SmartWebSocketV2
    ANGELONE_AVAILABLE = True
except ImportError:
    ANGELONE_AVAILABLE = False
    print("⚠️  SmartApi not installed. Run: pip install smartapi-python")

# Dhan imports  
try:
    from dhanhq import dhanhq
    DHAN_AVAILABLE = True
except ImportError:
    DHAN_AVAILABLE = False
    print("⚠️  DhanHQ not installed. Run: pip install dhanhq")

app = Flask(__name__)
CORS(app)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='threading')

# Global state
trading_state = {
    'mode': 'paper',
    'is_running': False,
    'positions': [],
    'orders': [],
    'pnl': {'realized': 0, 'unrealized': 0, 'total': 0},
    'market_data': {},
    'strategy': None,
    'subscribed_symbols': []
}

# API clients
angel_client = None
dhan_client = None
ws_client = None


class StrategyRunner:
    """Loads and runs custom user strategies"""
    
    def __init__(self, strategy_path):
        self.strategy_path = strategy_path
        self.strategy_module = None
        self.load_strategy()
    
    def load_strategy(self):
        spec = importlib.util.spec_from_file_location("user_strategy", self.strategy_path)
        self.strategy_module = importlib.util.module_from_spec(spec)
        sys.modules["user_strategy"] = self.strategy_module
        spec.loader.exec_module(self.strategy_module)
        
    def initialize(self, context):
        if hasattr(self.strategy_module, 'initialize'):
            self.strategy_module.initialize(context)
    
    def on_tick(self, context, tick_data):
        if hasattr(self.strategy_module, 'on_tick'):
            return self.strategy_module.on_tick(context, tick_data)
    
    def on_order_update(self, context, order):
        if hasattr(self.strategy_module, 'on_order_update'):
            self.strategy_module.on_order_update(context, order)


class TradingContext:
    """Context passed to strategy"""
    
    def __init__(self):
        self.positions = trading_state['positions']
        self.orders = trading_state['orders']
        self.market_data = trading_state['market_data']
        self.mode = trading_state['mode']
    
    def place_order(self, symbol, quantity, side, order_type='MARKET', price=None):
        order_data = {
            'symbol': symbol,
            'quantity': quantity,
            'side': side,
            'order_type': order_type,
            'price': price,
            'timestamp': datetime.now().isoformat(),
            'mode': trading_state['mode']
        }
        
        if trading_state['mode'] == 'live' and dhan_client and DHAN_AVAILABLE:
            try:
                response = dhan_client.place_order(
                    security_id=symbol,
                    exchange_segment=dhan_client.NSE,
                    transaction_type=dhan_client.BUY if side == 'BUY' else dhan_client.SELL,
                    quantity=quantity,
                    order_type=dhan_client.MARKET if order_type == 'MARKET' else dhan_client.LIMIT,
                    product_type=dhan_client.INTRA,
                    price=price if order_type == 'LIMIT' else 0
                )
                order_data['order_id'] = response['data']['orderId']
                order_data['status'] = 'PENDING'
                log_message(f"Live order placed: {response}", 'success')
            except Exception as e:
                log_message(f"Order failed: {str(e)}", 'error')
                order_data['status'] = 'FAILED'
        
        elif trading_state['mode'] == 'paper':
            order_data['order_id'] = f"PAPER_{int(datetime.now().timestamp())}"
            order_data['status'] = 'EXECUTED'
            
            if symbol in trading_state['market_data']:
                order_data['executed_price'] = trading_state['market_data'][symbol]['ltp']
            else:
                order_data['executed_price'] = price if price else 0
            
            log_message(f"Paper order: {side} {quantity} {symbol}", 'info')
        
        trading_state['orders'].append(order_data)
        socketio.emit('order_update', order_data)
        
        return order_data
    
    def get_positions(self):
        return self.positions
    
    def get_market_data(self, symbol):
        return self.market_data.get(symbol, {})


def on_tick(ws, tick):
    try:
        for token_data in tick:
            symbol = token_data.get('name', '')
            trading_state['market_data'][symbol] = {
                'ltp': token_data.get('last_traded_price', 0) / 100,
                'volume': token_data.get('volume_trade_for_the_day', 0),
                'timestamp': datetime.now().isoformat()
            }
        
        if trading_state['is_running'] and trading_state['strategy']:
            context = TradingContext()
            trading_state['strategy'].on_tick(context, tick)
        
        socketio.emit('market_data', trading_state['market_data'])
        
    except Exception as e:
        log_message(f"Tick error: {str(e)}", 'error')


def on_open(ws):
    log_message("WebSocket connected", 'success')


def on_close(ws):
    log_message("WebSocket disconnected", 'warning')


def on_error(ws, error):
    log_message(f"WebSocket error: {str(error)}", 'error')


# Create index.html file
def create_frontend_files():
    """Create static HTML file for frontend"""
    
    os.makedirs('static', exist_ok=True)
    
    html_content = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>AlgoTrading Platform</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <script src="https://cdn.socket.io/4.5.4/socket.io.min.js"></script>
    <style>
        @keyframes pulse {
            0%, 100% { opacity: 1; }
            50% { opacity: .5; }
        }
        .animate-pulse { animation: pulse 2s cubic-bezier(0.4, 0, 0.6, 1) infinite; }
    </style>
</head>
<body class="bg-gradient-to-br from-gray-900 via-gray-800 to-gray-900 text-white min-h-screen">
    <div id="root" class="p-6 max-w-7xl mx-auto">
        <div class="text-center py-20">
            <div class="text-6xl mb-4">🔄</div>
            <h2 class="text-2xl font-bold">Loading AlgoTrading Platform...</h2>
        </div>
    </div>

    <script>
        const socket = io();
        
        const app = {
            state: {
                activeMode: 'paper',
                isRunning: false,
                orders: [],
                pnl: { realized: 0, unrealized: 0, total: 0 },
                logs: [],
                strategyLoaded: false,
                strategyName: '',
                showSettings: false,
                config: {
                    angelone_api_key: '',
                    angelone_client_id: '',
                    angelone_password: '',
                    dhan_client_id: '',
                    dhan_access_token: ''
                }
            },

            init() {
                console.log('Initializing app...');
                this.render();
                this.setupSocketListeners();
                this.addLog('System initialized', 'success');
            },

            setupSocketListeners() {
                socket.on('connect', () => {
                    console.log('Socket connected');
                    this.addLog('Connected to server', 'success');
                });

                socket.on('log', (log) => {
                    this.state.logs.push(log);
                    if (this.state.logs.length > 100) this.state.logs.shift();
                    this.updateLogs();
                });

                socket.on('order_update', (order) => {
                    this.state.orders.push(order);
                    this.updateOrders();
                    this.addLog(`Order ${order.side}: ${order.symbol} x ${order.quantity}`, 'info');
                });

                socket.on('disconnect', () => {
                    console.log('Socket disconnected');
                    this.addLog('Disconnected from server', 'warning');
                });
            },

            async switchMode(mode) {
                this.state.activeMode = mode;
                this.addLog(`Switched to ${mode.toUpperCase()} mode`, 'info');
                this.render();
            },

            async uploadStrategy(e) {
                const file = e.target.files[0];
                if (!file || !file.name.endsWith('.py')) {
                    this.addLog('Please upload a valid Python (.py) file', 'error');
                    return;
                }

                const formData = new FormData();
                formData.append('file', file);

                try {
                    const response = await fetch('/api/strategy/upload', {
                        method: 'POST',
                        body: formData
                    });
                    const data = await response.json();
                    
                    if (data.status === 'success') {
                        this.state.strategyLoaded = true;
                        this.state.strategyName = data.filename;
                        this.addLog(`Strategy loaded: ${data.filename}`, 'success');
                        this.render();
                    } else {
                        this.addLog(`Strategy load failed: ${data.message}`, 'error');
                    }
                } catch (error) {
                    this.addLog(`Upload error: ${error.message}`, 'error');
                }
            },

            async startTrading() {
                if (!this.state.strategyLoaded) {
                    this.addLog('Please load a strategy first', 'error');
                    return;
                }

                try {
                    const response = await fetch('/api/trading/start', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ mode: this.state.activeMode })
                    });
                    const data = await response.json();
                    
                    if (data.status === 'success') {
                        this.state.isRunning = true;
                        this.addLog(`${this.state.activeMode.toUpperCase()} trading started`, 'success');
                        this.render();
                    } else {
                        this.addLog(`Start failed: ${data.message}`, 'error');
                    }
                } catch (error) {
                    this.addLog(`Start error: ${error.message}`, 'error');
                }
            },

            async stopTrading() {
                try {
                    const response = await fetch('/api/trading/stop', {
                        method: 'POST'
                    });
                    const data = await response.json();
                    
                    if (data.status === 'success') {
                        this.state.isRunning = false;
                        this.addLog('Trading stopped', 'warning');
                        this.render();
                    }
                } catch (error) {
                    this.addLog(`Stop failed: ${error.message}`, 'error');
                }
            },

            showSettings() {
                this.state.showSettings = true;
                this.render();
            },

            hideSettings() {
                this.state.showSettings = false;
                this.render();
            },

            async saveConfig() {
                try {
                    const response = await fetch('/api/config', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify(this.state.config)
                    });
                    const data = await response.json();
                    
                    if (data.status === 'success') {
                        this.addLog('API configuration saved', 'success');
                        this.hideSettings();
                    } else {
                        this.addLog(`Config failed: ${data.message}`, 'error');
                    }
                } catch (error) {
                    this.addLog(`Config error: ${error.message}`, 'error');
                }
            },

            updateConfig(field, value) {
                const keys = field.split('.');
                let obj = this.state;
                for (let i = 0; i < keys.length - 1; i++) {
                    obj = obj[keys[i]];
                }
                obj[keys[keys.length - 1]] = value;
            },

            addLog(message, type = 'info') {
                const log = {
                    timestamp: new Date().toLocaleTimeString(),
                    message,
                    type
                };
                this.state.logs.push(log);
                if (this.state.logs.length > 100) this.state.logs.shift();
                this.updateLogs();
            },

            updateLogs() {
                const logsEl = document.getElementById('logs-container');
                if (logsEl) {
                    logsEl.innerHTML = this.state.logs.map(log => `
                        <div class="${log.type === 'error' ? 'text-red-400' : 
                                      log.type === 'success' ? 'text-green-400' : 
                                      log.type === 'warning' ? 'text-yellow-400' : 
                                      'text-gray-300'} mb-1">
                            <span class="text-gray-500">[${log.timestamp}]</span> ${log.message}
                        </div>
                    `).join('');
                    logsEl.scrollTop = logsEl.scrollHeight;
                }
            },

            updateOrders() {
                const ordersEl = document.getElementById('orders-tbody');
                if (ordersEl) {
                    const ordersHtml = this.state.orders.slice(-10).reverse().map(order => `
                        <tr class="border-b border-gray-700">
                            <td class="py-3 px-2 text-sm">${new Date(order.timestamp).toLocaleTimeString()}</td>
                            <td class="py-3 px-2 font-semibold">${order.symbol}</td>
                            <td class="py-3 px-2 ${order.side === 'BUY' ? 'text-green-400' : 'text-red-400'}">${order.side}</td>
                            <td class="py-3 px-2">${order.quantity}</td>
                            <td class="py-3 px-2">${order.order_type}</td>
                            <td class="py-3 px-2">${order.executed_price || '-'}</td>
                            <td class="py-3 px-2">
                                <span class="px-2 py-1 rounded text-xs ${order.status === 'EXECUTED' ? 'bg-green-900 text-green-300' : 'bg-yellow-900 text-yellow-300'}">
                                    ${order.status}
                                </span>
                            </td>
                            <td class="py-3 px-2">
                                <span class="px-2 py-1 rounded text-xs ${
                                    order.mode === 'live' ? 'bg-red-900 text-red-300' : 
                                    order.mode === 'paper' ? 'bg-blue-900 text-blue-300' : 
                                    'bg-purple-900 text-purple-300'}">
                                    ${order.mode.toUpperCase()}
                                </span>
                            </td>
                        </tr>
                    `).join('');
                    
                    ordersEl.innerHTML = ordersHtml || '<tr><td colspan="8" class="py-8 text-center text-gray-500">No orders yet</td></tr>';
                }
            },

            render() {
                const { activeMode, isRunning, strategyLoaded, strategyName, pnl, showSettings, config } = this.state;
                
                document.getElementById('root').innerHTML = `
                    <div>
                        <!-- Header -->
                        <div class="flex justify-between items-center mb-6">
                            <h1 class="text-4xl font-bold bg-gradient-to-r from-blue-400 to-purple-400 bg-clip-text text-transparent">
                                AlgoTrading Platform
                            </h1>
                            <button onclick="app.showSettings()" class="p-3 bg-gray-700 hover:bg-gray-600 rounded-lg transition-colors">
                                ⚙️
                            </button>
                        </div>

                        <!-- Mode Switcher -->
                        <div class="flex gap-2 mb-6">
                            <button onclick="app.switchMode('paper')" class="px-6 py-3 rounded-lg font-semibold transition-all ${activeMode === 'paper' ? 'bg-blue-500 text-white shadow-lg' : 'bg-gray-700 text-gray-300 hover:bg-gray-600'}">
                                📈 Paper Trading
                            </button>
                            <button onclick="app.switchMode('backtest')" class="px-6 py-3 rounded-lg font-semibold transition-all ${activeMode === 'backtest' ? 'bg-purple-500 text-white shadow-lg' : 'bg-gray-700 text-gray-300 hover:bg-gray-600'}">
                                📊 Backtest
                            </button>
                            <button onclick="app.switchMode('live')" class="px-6 py-3 rounded-lg font-semibold transition-all ${activeMode === 'live' ? 'bg-red-500 text-white shadow-lg animate-pulse' : 'bg-gray-700 text-gray-300 hover:bg-gray-600'}">
                                💰 Live Trading
                            </button>
                        </div>

                        <!-- Main Controls -->
                        <div class="grid grid-cols-1 lg:grid-cols-3 gap-6 mb-6">
                            <!-- Strategy Upload -->
                            <div class="bg-gray-800 rounded-lg p-6 shadow-xl">
                                <h3 class="text-xl font-semibold mb-4">Strategy</h3>
                                <label class="flex flex-col items-center justify-center h-32 border-2 border-dashed border-gray-600 rounded-lg cursor-pointer hover:border-blue-500 transition-colors">
                                    <span class="text-4xl mb-2">📤</span>
                                    <span class="text-sm text-gray-400">Upload Python Strategy</span>
                                    <input type="file" accept=".py" onchange="app.uploadStrategy(event)" class="hidden">
                                </label>
                                ${strategyLoaded ? `
                                    <div class="mt-3 p-2 bg-green-900 bg-opacity-30 border border-green-600 rounded text-sm">
                                        ✓ ${strategyName}
                                    </div>
                                ` : ''}
                            </div>

                            <!-- Trading Controls -->
                            <div class="bg-gray-800 rounded-lg p-6 shadow-xl">
                                <h3 class="text-xl font-semibold mb-4">Controls</h3>
                                <div class="space-y-3">
                                    <button onclick="app.startTrading()" ${isRunning ? 'disabled' : ''} 
                                        class="w-full bg-green-600 hover:bg-green-700 disabled:bg-gray-600 disabled:cursor-not-allowed text-white py-3 rounded-lg font-semibold transition-colors">
                                        ▶️ Start Trading
                                    </button>
                                    <button onclick="app.stopTrading()" ${!isRunning ? 'disabled' : ''} 
                                        class="w-full bg-red-600 hover:bg-red-700 disabled:bg-gray-600 disabled:cursor-not-allowed text-white py-3 rounded-lg font-semibold transition-colors">
                                        ⏸️ Stop Trading
                                    </button>
                                </div>
                                ${isRunning ? '<div class="mt-3 flex items-center justify-center text-green-400">🔄 Running...</div>' : ''}
                            </div>

                            <!-- P&L Display -->
                            <div class="bg-gray-800 rounded-lg p-6 shadow-xl">
                                <h3 class="text-xl font-semibold mb-4">P&L Summary</h3>
                                <div class="space-y-2">
                                    <div class="flex justify-between">
                                        <span class="text-gray-400">Realized:</span>
                                        <span class="${pnl.realized >= 0 ? 'text-green-400' : 'text-red-400'}">
                                            ₹${pnl.realized.toFixed(2)}
                                        </span>
                                    </div>
                                    <div class="flex justify-between">
                                        <span class="text-gray-400">Unrealized:</span>
                                        <span class="${pnl.unrealized >= 0 ? 'text-green-400' : 'text-red-400'}">
                                            ₹${pnl.unrealized.toFixed(2)}
                                        </span>
                                    </div>
                                    <div class="flex justify-between pt-2 border-t border-gray-700">
                                        <span class="font-semibold">Total P&L:</span>
                                        <span class="font-bold ${pnl.total >= 0 ? 'text-green-400' : 'text-red-400'}">
                                            ₹${pnl.total.toFixed(2)}
                                        </span>
                                    </div>
                                </div>
                            </div>
                        </div>

                        <!-- Orders Table -->
                        <div class="bg-gray-800 rounded-lg p-6 shadow-xl mb-6">
                            <h3 class="text-xl font-semibold mb-4">Orders</h3>
                            <div class="overflow-x-auto">
                                <table class="w-full">
                                    <thead>
                                        <tr class="text-left border-b border-gray-700">
                                            <th class="pb-3 px-2">Time</th>
                                            <th class="pb-3 px-2">Symbol</th>
                                            <th class="pb-3 px-2">Side</th>
                                            <th class="pb-3 px-2">Qty</th>
                                            <th class="pb-3 px-2">Type</th>
                                            <th class="pb-3 px-2">Price</th>
                                            <th class="pb-3 px-2">Status</th>
                                            <th class="pb-3 px-2">Mode</th>
                                        </tr>
                                    </thead>
                                    <tbody id="orders-tbody">
                                        <tr><td colspan="8" class="py-8 text-center text-gray-500">No orders yet</td></tr>
                                    </tbody>
                                </table>
                            </div>
                        </div>

                        <!-- Logs Panel -->
                        <div class="bg-gray-800 rounded-lg p-6 shadow-xl">
                            <h3 class="text-xl font-semibold mb-4">System Logs</h3>
                            <div id="logs-container" class="bg-gray-900 rounded p-4 h-64 overflow-y-auto font-mono text-sm">
                                <div class="text-gray-500">System ready. Load a strategy to begin.</div>
                            </div>
                        </div>

                        <!-- Settings Modal -->
                        ${showSettings ? `
                            <div class="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50">
                                <div class="bg-gray-800 rounded-lg p-6 w-full max-w-2xl max-h-[90vh] overflow-y-auto">
                                    <h2 class="text-2xl font-bold mb-4">API Configuration</h2>
                                    
                                    <div class="space-y-4">
                                        <div>
                                            <h3 class="text-lg font-semibold text-blue-400 mb-2">AngelOne API</h3>
                                            <input type="text" placeholder="API Key" value="${config.angelone_api_key}"
                                                onchange="app.updateConfig('config.angelone_api_key', this.value)"
                                                class="w-full p-2 bg-gray-700 text-white rounded mb-2">
                                            <input type="text" placeholder="Client ID" value="${config.angelone_client_id}"
                                                onchange="app.updateConfig('config.angelone_client_id', this.value)"
                                                class="w-full p-2 bg-gray-700 text-white rounded mb-2">
                                            <input type="password" placeholder="Password" value="${config.angelone_password}"
                                                onchange="app.updateConfig('config.angelone_password', this.value)"
                                                class="w-full p-2 bg-gray-700 text-white rounded">
                                        </div>

                                        <div>
                                            <h3 class="text-lg font-semibold text-red-400 mb-2">Dhan API (Live Trading)</h3>
                                            <input type="text" placeholder="Client ID" value="${config.dhan_client_id}"
                                                onchange="app.updateConfig('config.dhan_client_id', this.value)"
                                                class="w-full p-2 bg-gray-700 text-white rounded mb-2">
                                            <input type="text" placeholder="Access Token" value="${config.dhan_access_token}"
                                                onchange="app.updateConfig('config.dhan_access_token', this.value)"
                                                class="w-full p-2 bg-gray-700 text-white rounded">
                                        </div>
                                    </div>

                                    <div class="flex gap-3 mt-6">
                                        <button onclick="app.saveConfig()" class="flex-1 bg-green-600 hover:bg-green-700 text-white py-2 rounded-lg font-semibold">
                                            Save Configuration
                                        </button>
                                        <button onclick="app.hideSettings()" class="flex-1 bg-gray-600 hover:bg-gray-700 text-white py-2 rounded-lg font-semibold">
                                            Cancel
                                        </button>
                                    </div>
                                </div>
                            </div>
                        ` : ''}
                    </div>
                `;
            }
        };

        // Initialize app on page load
        window.addEventListener('DOMContentLoaded', () => {
            console.log('DOM loaded, initializing app...');
            app.init();
        });
    </script>
</body>
</html>"""
    
    with open('static/index.html', 'w', encoding='utf-8') as f:
        f.write(html_content)
    
    print("✅ Frontend files created in ./static/")


# Routes

@app.route('/')
def index():
    return send_from_directory('static', 'index.html')


@app.route('/api/config', methods=['POST'])
def configure_apis():
    global angel_client, dhan_client, ws_client
    
    config = request.json
    
    if not ANGELONE_AVAILABLE:
        return jsonify({'status': 'error', 'message': 'SmartApi not installed'}), 400
    
    try:
        angel_client = SmartConnect(api_key=config['angelone_api_key'])
        
        session = angel_client.generateSession(
            clientCode=config['angelone_client_id'],
            password=config['angelone_password']
        )
        
        feed_token = angel_client.getfeedToken()
        ws_client = SmartWebSocketV2(
            auth_token=session['data']['jwtToken'],
            api_key=config['angelone_api_key'],
            client_code=config['angelone_client_id'],
            feed_token=feed_token
        )
        
        ws_client.on_open = on_open
        ws_client.on_data = on_tick
        ws_client.on_error = on_error
        ws_client.on_close = on_close
        
        if DHAN_AVAILABLE:
            dhan_client = dhanhq(config['dhan_client_id'], config['dhan_access_token'])
        
        log_message("APIs configured successfully", 'success')
        return jsonify({'status': 'success'})
        
    except Exception as e:
        log_message(f"Config failed: {str(e)}", 'error')
        return jsonify({'status': 'error', 'message': str(e)}), 400


@app.route('/api/strategy/upload', methods=['POST'])
def upload_strategy():
    if 'file' not in request.files:
        return jsonify({'status': 'error', 'message': 'No file provided'}), 400
    
    file = request.files['file']
    
    if not file.filename.endswith('.py'):
        return jsonify({'status': 'error', 'message': 'Invalid Python file'}), 400
    
    try:
        strategy_path = os.path.join('strategies', file.filename)
        os.makedirs('strategies', exist_ok=True)
        file.save(strategy_path)
        
        trading_state['strategy'] = StrategyRunner(strategy_path)
        
        log_message(f"Strategy loaded: {file.filename}", 'success')
        return jsonify({'status': 'success', 'filename': file.filename})
        
    except Exception as e:
        log_message(f"Strategy load failed: {str(e)}", 'error')
        return jsonify({'status': 'error', 'message': str(e)}), 400


@app.route('/api/trading/start', methods=['POST'])
def start_trading():
    data = request.json
    trading_state['mode'] = data.get('mode', 'paper')
    
    if not trading_state['strategy']:
        return jsonify({'status': 'error', 'message': 'No strategy loaded'}), 400
    
    if trading_state['is_running']:
        return jsonify({'status': 'error', 'message': 'Already running'}), 400
    
    try:
        trading_state['is_running'] = True
        
        context = TradingContext()
        trading_state['strategy'].initialize(context)
        
        if ws_client and trading_state['mode'] != 'backtest':
            ws_client.connect()
        
        log_message(f"Trading started in {trading_state['mode']} mode", 'success')
        return jsonify({'status': 'success'})
        
    except Exception as e:
        trading_state['is_running'] = False
        log_message(f"Start failed: {str(e)}", 'error')
        return jsonify({'status': 'error', 'message': str(e)}), 400


@app.route('/api/trading/stop', methods=['POST'])
def stop_trading():
    trading_state['is_running'] = False
    
    if ws_client:
        try:
            ws_client.close_connection()
        except:
            pass
    
    log_message("Trading stopped", 'warning')
    return jsonify({'status': 'success'})


@app.route('/api/positions', methods=['GET'])
def get_positions():
    if trading_state['mode'] == 'live' and dhan_client and DHAN_AVAILABLE:
        try:
            positions = dhan_client.get_positions()
            return jsonify({'status': 'success', 'positions': positions['data']})
        except Exception as e:
            return jsonify({'status': 'error', 'message': str(e)}), 400
    else:
        return jsonify({'status': 'success', 'positions': trading_state['positions']})


@app.route('/api/orders', methods=['GET'])
def get_orders():
    return jsonify({'status': 'success', 'orders': trading_state['orders']})


@app.route('/api/pnl', methods=['GET'])
def get_pnl():
    return jsonify({'status': 'success', 'pnl': trading_state['pnl']})


@app.route('/api/status', methods=['GET'])
def get_status():
    """Get current system status"""
    return jsonify({
        'status': 'success',
        'data': {
            'mode': trading_state['mode'],
            'is_running': trading_state['is_running'],
            'strategy_loaded': trading_state['strategy'] is not None,
            'angelone_connected': angel_client is not None,
            'dhan_connected': dhan_client is not None,
            'orders_count': len(trading_state['orders']),
            'positions_count': len(trading_state['positions'])
        }
    })


def log_message(message, level='info'):
    """Log message and emit to frontend"""
    log_entry = {
        'timestamp': datetime.now().strftime('%H:%M:%S'),
        'message': message,
        'type': level
    }
    
    print(f"[{log_entry['timestamp']}] {level.upper()}: {message}")
    socketio.emit('log', log_entry)


@socketio.on('connect')
def handle_connect():
    log_message("Frontend connected", 'info')
    emit('connection_status', {'connected': True})


@socketio.on('disconnect')
def handle_disconnect():
    log_message("Frontend disconnected", 'info')


def open_browser():
    """Open browser after short delay"""
    import time
    time.sleep(2)
    try:
        webbrowser.open('http://localhost:5000')
    except:
        print("⚠️  Could not open browser automatically. Please open http://localhost:5000 manually")


if __name__ == '__main__':
    import os
    
    # Create directories
    os.makedirs('strategies', exist_ok=True)
    os.makedirs('static', exist_ok=True)
    
    # Create frontend files
    print("\n📁 Creating frontend files...")
    create_frontend_files()
    
    # Print banner
    print("\n" + "=" * 70)
    print("      🚀 ALGOTRADING PLATFORM - DEPLOYED VERSION")
    print("=" * 70)
    
    # Get port from environment (Railway provides this)
    port = int(os.environ.get('PORT', 5000))
    
    print(f"\n🌐 Server starting on port: {port}")
    print("=" * 70)
    
    # Run the app (no browser opening in production)
    socketio.run(app, host='0.0.0.0', port=port, debug=False, allow_unsafe_werkzeug=True)