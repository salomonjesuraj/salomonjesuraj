import { fetchBrokerHoldings, fetchBrokerOrders, fetchBrokerPositions } from '../lib/api'
import { useBrokerRefreshStore } from '../store/useBrokerRefreshStore'
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
 *
 * "Telegram Redesign & Token Modal" sprint (2026-08-27): all three now
 * also depend on useBrokerRefreshStore's nonce, so a fresh token save
 * can force an immediate refetch instead of waiting out each hook's own
 * interval -- see that store's own docstring for why.
 */

export function useActivePositions() {
  const refreshNonce = useBrokerRefreshStore((s) => s.nonce)
  return usePolling(fetchBrokerPositions, 3000, [refreshNonce])
}

export function useOrderBook() {
  const refreshNonce = useBrokerRefreshStore((s) => s.nonce)
  return usePolling(fetchBrokerOrders, 15000, [refreshNonce])
}

export function useHoldings() {
  const refreshNonce = useBrokerRefreshStore((s) => s.nonce)
  return usePolling(fetchBrokerHoldings, 60000, [refreshNonce])
}
