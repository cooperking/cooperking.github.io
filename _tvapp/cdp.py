"""Runs JavaScript in the app's page through the browser view's debugging socket (adb forward tcp:9222 first)."""
import json, sys, urllib.request
import websocket
tabs = json.load(urllib.request.urlopen('http://127.0.0.1:9222/json'))
tab = [t for t in tabs if 'cooperking' in t.get('url', '')][0]
ws = websocket.create_connection(tab['webSocketDebuggerUrl'], suppress_origin=True)
ws.send(json.dumps({'id': 1, 'method': 'Runtime.evaluate', 'params': {'expression': sys.argv[1], 'returnByValue': True}}))
print(ws.recv())
