import { fetchBrokerHoldings, fetchBrokerOrders, fetchBrokerPositions } from '../lib/api'
import { usePolling } from './usePolling'

/**
 * "Broker Sync & Active Position Intelligence" master sprint
 * (2026-08-27) -- three independent polling hooks, one per real
 * endpoint, each at the cadence the sprint's own spec asked for:
 * positions/M2M fastest (real money moving live), the order book next
 * (a manual fill can land any time but doesn't need sub-5s freshness),
 * holdings slowest (delivery equity, moves at most once a day). All
 * three are read-only GETs -- see api/broker_sync.py's own module
 * docstring for the full "no order placement anywhere" disclosure.
 */

export function useActivePositions() {
  return usePolling(fetchBrokerPositions, 3000, [])
}

export function useOrderBook() {
  return usePolling(fetchBrokerOrders, 15000, [])
}

export function useHoldings() {
  return usePolling(fetchBrokerHoldings, 60000, [])
}
