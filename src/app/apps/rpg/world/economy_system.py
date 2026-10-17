"""Economy Simulation System - TIER 8: World Complexity Layer.

This module implements PART 1 of Tier 8 from the RPG design specification:
the Economy Simulation.

Purpose:
    Turn economic pressure into emergent story. Supply and demand per
    region create scarcity, trade routes create opportunity for faction
    conflict, and price fluctuations create urgency for quests.

The Problem:
    - World economy is static and abstract
    - No shortage-driven quest opportunities
    - Faction conflicts lack economic motivation
    - Player has no economic impact on world

The Solution:
    EconomySystem tracks supply/demand per good per location, simulates
    trade flow between markets, and generates economic events (shortages,
    trade spikes, shortages) that feed into the quest generator and
    faction conflict engine.

Architecture:
    Market (per location):
        goods: {good -> {supply, demand, price}}
        
    EconomySystem:
        markets: {location_id -> Market}
        trade_routes: [(src, dst, good)]
        
        update():
            1. Update local prices based on supply/demand
            2. Simulate trade flow along routes
            3. Return economic events

Key Features:
    - Supply/demand price simulation
    - Trade route flow detection
    - Shortage event generation
    - Market state tracking
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Tuple


@dataclass
class Market:
    """A local market tracking supply, demand, and prices for goods.
    
    Markets exist at specific locations and reflect local economic
    conditions. Scarcity drives prices up; oversupply drives them down.
    
    Attributes:
        location_id: Unique location identifier.
        goods: Dict of good name -> {supply, demand, price}.
    """
    
    location_id: str
    goods: Dict[str, Dict[str, float]] = field(default_factory=dict)
    
    def add_good(
        self,
        good: str,
        supply: float = 1.0,
        demand: float = 1.0,
        price: float = 1.0,
    ) -> None:
        """Add or update a good in this market.
        
        Args:
            good: Good name (e.g., "food", "iron", "herbs").
            supply: Available supply (0.0 to 1.0+, higher = more supply).
            demand: Local demand (0.0 to 1.0+, higher = more demand).
            price: Base price (1.0 is neutral).
        """
        self.goods[good] = {
            "supply": max(0.0, supply),
            "demand": max(0.0, demand),
            "price": max(0.01, price),
        }
    
    def update_prices(self) -> List[Dict[str, Any]]:
        """Update prices based on supply/demand ratio.
        
        Price mechanics:
            - High supply relative to demand: price drops ~10%
            - Low supply relative to demand: price rises ~50%
            - Zero supply: price spikes 50%
            
        Returns:
            List of price change events for significant shifts.
        """
        events: List[Dict[str, Any]] = []
        
        for good, data in self.goods.items():
            supply = data["supply"]
            demand = data["demand"]
            old_price = data["price"]
            
            if supply <= 0:
                # Complete shortage - price spikes
                data["price"] *= 1.5
            else:
                ratio = demand / supply
                # Price adjustment: 10% per unit of imbalance
                data["price"] *= (1 + (ratio - 1) * 0.1)
            
            # Clamp price to reasonable range
            data["price"] = max(0.01, min(10.0, data["price"]))
            
            # Detect significant price changes (>20% shift)
            price_change = abs(data["price"] - old_price) / max(0.01, old_price)
            if price_change > 0.2:
                direction = "spike" if data["price"] > old_price else "drop"
                events.append({
                    "type": "price_change",
                    "good": good,
                    "location": self.location_id,
                    "direction": direction,
                    "old_price": old_price,
                    "new_price": data["price"],
                    "importance": min(1.0, price_change),
                })
        
        # Detect shortages (supply < 0.2)
        for good, data in self.goods.items():
            if data["supply"] < 0.2:
                events.append({
                    "type": "shortage",
                    "good": good,
                    "location": self.location_id,
                    "supply": data["supply"],
                    "price": data["price"],
                    "importance": 0.6 + (0.2 - data["supply"]) * 2.0,
                })
        
        return events


class EconomySystem:
    """Simulates marketplace dynamics across multiple locations.
    
    The EconomySystem tracks trade flows and generates economic events
    that can trigger quests, faction conflicts, or narrative beats.
    It operates independently of player action - the market evolves
    whether or not the player participates.
    
    Integration Points:
        - Dynamic Quest Generator: Shortages → supply quests, price spikes → trade quests
        - Faction System: Trade conflicts over valuable routes
        - PlayerLoop: Economic events added to world event stream
        - WorldState: Markets can be placed in locations
    
    Usage:
        economy = EconomySystem()
        
        # Markets
        town = Market("town")
        town.add_good("food", supply=0.5, demand=1.0, price=1.0)
        town.add_good("iron", supply=1.0, demand=0.3, price=0.8)
        economy.add_market(town)
        
        # Trade routes
        economy.add_trade_route("town", "mine", "iron")
        
        # Each tick
        events = economy.update()
        for event in events:
            if event["type"] == "shortage":
                quest_gen.create_supply_quest(event["location"], event["good"])
    """
    
    def __init__(self):
        """Initialize the EconomySystem with empty markets."""
        self.markets: Dict[str, Market] = {}
        self.trade_routes: List[Tuple[str, str, str]] = []
        
    def add_market(self, market: Market) -> None:
        """Register a market at a location.
        
        Args:
            market: Market object to add.
        """
        self.markets[market.location_id] = market
        
    def add_trade_route(self, source: str, destination: str, good: str) -> None:
        """Add a trade route between two locations.
        
        Trade routes allow goods to flow from source to destination
        when price differences justify the trade.
        
        Args:
            source: Source location ID.
            destination: Destination location ID.
            good: Good being traded.
        """
        # Avoid duplicate routes
        route = (source, destination, good)
        if route not in self.trade_routes:
            self.trade_routes.append(route)
    
    def update(self) -> List[Dict[str, Any]]:
        """Advance the economy simulation by one tick.
        
        Processes all markets in order:
        1. Update local prices based on supply/demand
        2. Simulate trade flow along routes
        
        Returns:
            List of economic events:
            - "price_change": Significant price movement
            - "shortage": Supply dropped below threshold
            - "trade_flow": Active trade between locations
        """
        events: List[Dict[str, Any]] = []
        
        # Phase 1: Update local market prices
        for market in self.markets.values():
            price_events = market.update_prices()
            events.extend(price_events)
        
        # Phase 2: Simulate trade flow
        events.extend(self._simulate_trade())
        
        return events
    
    def _simulate_trade(self) -> List[Dict[str, Any]]:
        """Simulate trade flow along established routes.
        
        Trade occurs when price difference between source and destination
        exceeds the trade threshold (0.2). When trade flows, supply
        moves from source to destination.
        
        Returns:
            List of trade flow events.
        """
        events: List[Dict[str, Any]] = []
        
        for src_id, dst_id, good in self.trade_routes:
            src_market = self.markets.get(src_id)
            dst_market = self.markets.get(dst_id)
            
            if not src_market or not dst_market:
                continue
            
            src_goods = src_market.goods.get(good)
            dst_goods = dst_market.goods.get(good)
            
            if not src_goods or not dst_goods:
                continue
            
            # Price difference drives trade
            price_diff = dst_goods["price"] - src_goods["price"]
            
            # Trade occurs when price differential is significant
            if price_diff > 0.2:
                # Trade volume scales with price difference
                trade_volume = min(0.1, price_diff * 0.05)
                
                # Move supply from source to destination
                src_goods["supply"] = max(0.0, src_goods["supply"] - trade_volume)
                dst_goods["supply"] += trade_volume * 0.8  # Some loss in transit
                
                events.append({
                    "type": "trade_flow",
                    "good": good,
                    "from": src_id,
                    "to": dst_id,
                    "volume": trade_volume,
                    "price_diff": price_diff,
                    "importance": 0.4,
                })
        
        return events
    
    def reset(self) -> None:
        """Clear all market and trade route data."""
        self.markets.clear()
        self.trade_routes.clear()