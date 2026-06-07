# Dashboard Integration Guide

To connect your **Next.js Dashboard** with the **Quant Trader Python Engine** and **Supabase**, you need to implement three layers:
1. **API Proxy Route**: Route frontend requests safely to the Python engine (which runs locally on port `8001` or a custom host).
2. **Supabase Reporting Layer**: Fetch trade records and trade events from Supabase.
3. **Dashboard UI Pages**: Display analysis, decisions, active trades, and historical PnL.

Here is the complete implementation code for these layers.

---

## 1. Supabase Setup & Server Actions (`lib/supabase.ts`)
Create a server-side client to pull the canonical trade records and events. Next.js should read from Supabase for all metrics and history.

```typescript
import { createClient } from '@supabase/supabase-js';

const supabaseUrl = process.env.NEXT_PUBLIC_SUPABASE_URL || '';
const supabaseKey = process.env.SUPABASE_SERVICE_ROLE_KEY || ''; // Use service role key on servers

export const supabase = createClient(supabaseUrl, supabaseKey);

export interface Trade {
  id: string;
  symbol: string;
  direction: 'LONG' | 'SHORT';
  status: 'ACTIVE' | 'CLOSED';
  entry_price: number;
  stop_loss: number;
  take_profit_1: number;
  take_profit_2: number;
  quantity: number;
  leverage: number;
  risk_pct: number;
  executed_at: string;
  closed_at?: string;
  close_reason?: string;
  final_pnl?: number;
}

export interface TradeEvent {
  id: string;
  trade_id: string;
  event_type: string;
  value?: number;
  details?: any;
  executed_at: string;
}

/**
 * Fetch all trade history
 */
export async function getTradeHistory(): Promise<Trade[]> {
  const { data, error } = await supabase
    .from('trades')
    .select('*')
    .order('executed_at', { ascending: false });

  if (error) {
    console.error('Error fetching trade history:', error);
    return [];
  }
  return data || [];
}

/**
 * Fetch active trades
 */
export async function getActiveTrades(): Promise<Trade[]> {
  const { data, error } = await supabase
    .from('trades')
    .select('*')
    .eq('status', 'ACTIVE')
    .order('executed_at', { ascending: false });

  if (error) {
    console.error('Error fetching active trades:', error);
    return [];
  }
  return data || [];
}

/**
 * Fetch trade events timeline for a specific trade
 */
export async function getTradeEvents(tradeId: string): Promise<TradeEvent[]> {
  const { data, error } = await supabase
    .from('trade_events')
    .select('*')
    .eq('trade_id', tradeId)
    .order('executed_at', { ascending: true });

  if (error) {
    console.error('Error fetching trade events:', error);
    return [];
  }
  return data || [];
}
```

---

## 2. Next.js API Proxy Routes (`app/api/engine/[...path]/route.ts`)
If using Next.js **App Router**, create this catch-all API proxy route to forward calls to the Python FastAPI engine (e.g. `http://localhost:8001`). This bypasses CORS issues.

```typescript
import { NextRequest, NextResponse } from 'next/server';

const PYTHON_ENGINE_URL = process.env.PYTHON_ENGINE_URL || 'http://localhost:8001';

export async function GET(request: NextRequest, { params }: { params: { path: string[] } }) {
  const path = params.path.join('/');
  const searchParams = request.nextUrl.searchParams.toString();
  const url = `${PYTHON_ENGINE_URL}/api/v1/${path}${searchParams ? `?${searchParams}` : ''}`;

  try {
    const response = await fetch(url, {
      method: 'GET',
      headers: {
        'Content-Type': 'application/json',
      },
      next: { revalidate: 0 } // Disable caching
    });

    if (!response.ok) {
      return NextResponse.json(
        { error: `Engine returned status ${response.status}` },
        { status: response.status }
      );
    }

    const data = await response.json();
    return NextResponse.json(data);
  } catch (error: any) {
    console.error(`Proxy GET error for /api/v1/${path}:`, error);
    return NextResponse.json({ error: 'Failed to connect to Python trading engine' }, { status: 502 });
  }
}

export async function POST(request: NextRequest, { params }: { params: { path: string[] } }) {
  const path = params.path.join('/');
  const url = `${PYTHON_ENGINE_URL}/api/v1/${path}`;

  try {
    const body = await request.json();
    const response = await fetch(url, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(body),
    });

    const data = await response.json();
    return NextResponse.json(data, { status: response.status });
  } catch (error: any) {
    console.error(`Proxy POST error for /api/v1/${path}:`, error);
    return NextResponse.json({ error: 'Failed to connect to Python trading engine' }, { status: 502 });
  }
}
```

---

## 3. Dashboard Frontend Component (`app/dashboard/page.tsx`)
A complete, premium, responsive React dashboard showing live analysis, decisions, active trades, and historical PnL:

```tsx
'use client';

import React, { useEffect, useState } from 'react';

// Definitions for state
interface Decision {
  symbol: string;
  decision: string;
  direction: string;
  confidence_score: number;
  aggregate_bias_score: number;
  reason: string;
  requires_manual_confirmation: boolean;
}

interface Trade {
  id: string;
  symbol: string;
  direction: 'LONG' | 'SHORT';
  status: 'ACTIVE' | 'CLOSED';
  entry_price: number;
  stop_loss: number;
  take_profit_1: number;
  take_profit_2: number;
  quantity: number;
  executed_at: string;
  final_pnl?: number;
}

export default function Dashboard() {
  const [engineStatus, setEngineStatus] = useState<'ONLINE' | 'OFFLINE'>('OFFLINE');
  const [decision, setDecision] = useState<Decision | null>(null);
  const [activeTrades, setActiveTrades] = useState<Trade[]>([]);
  const [tradeHistory, setTradeHistory] = useState<Trade[]>([]);
  const [loading, setLoading] = useState(true);

  async function fetchDashboardData() {
    try {
      // 1. Fetch Engine Live Decision & Health Status
      const decisionRes = await fetch('/api/engine/decision/evaluate?symbol=BTC/USDT');
      if (decisionRes.ok) {
        const decisionData = await decisionRes.json();
        setDecision(decisionData);
        setEngineStatus('ONLINE');
      } else {
        setEngineStatus('OFFLINE');
      }

      // 2. Fetch Active Trades (e.g. from Supabase API route or direct server actions)
      const activeRes = await fetch('/api/supabase/active-trades'); // Map this to your database getActiveTrades action
      if (activeRes.ok) {
        const activeData = await activeRes.json();
        setActiveTrades(activeData);
      }

      // 3. Fetch History (from Supabase getTradeHistory action)
      const historyRes = await fetch('/api/supabase/trade-history');
      if (historyRes.ok) {
        const historyData = await historyRes.json();
        setTradeHistory(historyData);
      }
    } catch (err) {
      console.error('Failed to load dashboard data:', err);
      setEngineStatus('OFFLINE');
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    fetchDashboardData();
    const interval = setInterval(fetchDashboardData, 10000); // Poll every 10s
    return () => clearInterval(interval);
  }, []);

  const totalPnL = tradeHistory.reduce((sum, t) => sum + (t.final_pnl || 0), 0);

  return (
    <div className="min-h-screen bg-slate-950 text-slate-100 p-8 font-sans">
      <header className="flex justify-between items-center mb-8 border-b border-slate-800 pb-4">
        <div>
          <h1 className="text-3xl font-extrabold tracking-tight text-white">Quant Trader Dashboard</h1>
          <p className="text-slate-400 mt-1">Live Technical Analysis, Alpha Vetoes, and Execution Bridge</p>
        </div>
        <div className="flex items-center gap-3 bg-slate-900 px-4 py-2 rounded-lg border border-slate-800">
          <span className={`w-3.5 h-3.5 rounded-full ${engineStatus === 'ONLINE' ? 'bg-emerald-500 animate-pulse' : 'bg-rose-500'}`}></span>
          <span className="font-semibold text-sm">{engineStatus === 'ONLINE' ? 'ENGINE ACTIVE' : 'ENGINE DISCONNECTED'}</span>
        </div>
      </header>

      {loading ? (
        <div className="flex justify-center items-center h-64 text-slate-400">Loading live data...</div>
      ) : (
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
          {/* Main Column */}
          <div className="lg:col-span-2 space-y-6">
            {/* Live Decision Panel */}
            <section className="bg-slate-900 border border-slate-800 rounded-xl p-6 shadow-md">
              <h2 className="text-xl font-bold mb-4 text-slate-200">Current Trade Decision (BTC/USDT)</h2>
              {decision ? (
                <div>
                  <div className="flex justify-between items-center mb-4">
                    <div className="text-sm text-slate-400">Decision Status:</div>
                    <div className={`px-4 py-1.5 rounded-full font-bold text-sm ${
                      decision.decision.startsWith('APPROVE') ? 'bg-emerald-950 text-emerald-400 border border-emerald-800' :
                      decision.decision === 'WAIT' ? 'bg-amber-950 text-amber-400 border border-amber-800' :
                      'bg-rose-950 text-rose-400 border border-rose-800'
                    }`}>
                      {decision.decision}
                    </div>
                  </div>
                  <div className="grid grid-cols-2 gap-4 bg-slate-950 p-4 rounded-lg border border-slate-800 mb-4">
                    <div>
                      <div className="text-xs text-slate-400">Aggregate Bias Score</div>
                      <div className="text-lg font-mono font-bold mt-1">
                        {decision.aggregate_bias_score > 0 ? '+' : ''}{decision.aggregate_bias_score}
                      </div>
                    </div>
                    <div>
                      <div className="text-xs text-slate-400">Confidence Score</div>
                      <div className="text-lg font-mono font-bold mt-1">{(decision.confidence_score * 100).toFixed(1)}%</div>
                    </div>
                  </div>
                  <div className="p-3 bg-slate-850 rounded border border-slate-800 text-sm">
                    <span className="font-semibold text-slate-300">Reasoning Details:</span>
                    <p className="text-slate-400 mt-1 leading-relaxed">{decision.reason}</p>
                  </div>
                </div>
              ) : (
                <p className="text-slate-400">No live signal decisions evaluated yet.</p>
              )}
            </section>

            {/* Active Position Tracking */}
            <section className="bg-slate-900 border border-slate-800 rounded-xl p-6 shadow-md">
              <h2 className="text-xl font-bold mb-4 text-slate-200">Active Positions ({activeTrades.length})</h2>
              {activeTrades.length > 0 ? (
                <div className="overflow-x-auto">
                  <table className="w-full text-left text-sm">
                    <thead>
                      <tr className="border-b border-slate-800 text-slate-400">
                        <th className="py-2">Symbol</th>
                        <th className="py-2">Direction</th>
                        <th className="py-2">Entry Price</th>
                        <th className="py-2">Stop Loss</th>
                        <th className="py-2">TP1 / TP2</th>
                        <th className="py-2">Qty</th>
                      </tr>
                    </thead>
                    <tbody>
                      {activeTrades.map((t) => (
                        <tr key={t.id} className="border-b border-slate-800/50 hover:bg-slate-850/50">
                          <td className="py-3 font-semibold text-white">{t.symbol}</td>
                          <td className={`py-3 font-bold ${t.direction === 'LONG' ? 'text-emerald-400' : 'text-rose-400'}`}>
                            {t.direction}
                          </td>
                          <td className="py-3 font-mono">{t.entry_price.toLocaleString()}</td>
                          <td className="py-3 font-mono text-rose-300">{t.stop_loss.toLocaleString()}</td>
                          <td className="py-3 font-mono text-emerald-300">
                            {t.take_profit_1.toLocaleString()} / {t.take_profit_2.toLocaleString()}
                          </td>
                          <td className="py-3 font-mono">{t.quantity}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : (
                <p className="text-slate-500">No active positions currently monitored by the engine.</p>
              )}
            </section>
          </div>

          {/* Sidebar Column */}
          <div className="space-y-6">
            {/* PnL Card */}
            <section className="bg-slate-900 border border-slate-800 rounded-xl p-6 shadow-md">
              <h2 className="text-lg font-bold mb-2 text-slate-400">Total Realized PnL</h2>
              <div className={`text-4xl font-extrabold font-mono ${totalPnL >= 0 ? 'text-emerald-400' : 'text-rose-400'}`}>
                {totalPnL >= 0 ? '+' : ''}{totalPnL.toFixed(4)} USDT
              </div>
            </section>

            {/* Completed Trade Ledger */}
            <section className="bg-slate-900 border border-slate-800 rounded-xl p-6 shadow-md">
              <h2 className="text-xl font-bold mb-4 text-slate-200">Historical Ledger</h2>
              {tradeHistory.length > 0 ? (
                <div className="space-y-4 max-h-96 overflow-y-auto pr-1">
                  {tradeHistory.map((t) => (
                    <div key={t.id} className="flex justify-between items-center p-3 bg-slate-950 rounded-lg border border-slate-850">
                      <div>
                        <div className="flex items-center gap-2">
                          <span className="font-bold text-white text-sm">{t.symbol}</span>
                          <span className={`text-xs font-bold px-1.5 py-0.5 rounded ${t.direction === 'LONG' ? 'bg-emerald-950 text-emerald-400' : 'bg-rose-950 text-rose-400'}`}>
                            {t.direction}
                          </span>
                        </div>
                        <span className="text-xs text-slate-500 mt-1 block">
                          {new Date(t.executed_at).toLocaleString()}
                        </span>
                      </div>
                      <div className="text-right">
                        <div className={`font-mono font-bold ${t.final_pnl && t.final_pnl >= 0 ? 'text-emerald-400' : 'text-rose-400'}`}>
                          {t.final_pnl ? `${t.final_pnl >= 0 ? '+' : ''}${t.final_pnl.toFixed(2)}` : '0.00'}
                        </div>
                        <span className="text-xs text-slate-500">USDT</span>
                      </div>
                    </div>
                  ))}
                </div>
              ) : (
                <p className="text-slate-500">No trade ledger history available.</p>
              )}
            </section>
          </div>
        </div>
      )}
    </div>
  );
}
```
