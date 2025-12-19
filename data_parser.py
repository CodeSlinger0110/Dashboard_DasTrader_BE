"""
Parser for DasTrader CMD API responses
"""
import re
import logging
from typing import List, Dict, Optional
from datetime import datetime

logger = logging.getLogger(__name__)
try:
    from .constants import (
        MARKER_POS_START, MARKER_POS_END,
        MARKER_ORDER_START, MARKER_ORDER_END,
        MARKER_TRADE_START, MARKER_TRADE_END,
        POS_TYPE_CASH, POS_TYPE_MARGIN, POS_TYPE_SHORT
    )
except ImportError:
    from constants import (
        MARKER_POS_START, MARKER_POS_END,
        MARKER_ORDER_START, MARKER_ORDER_END,
        MARKER_TRADE_START, MARKER_TRADE_END,
        POS_TYPE_CASH, POS_TYPE_MARGIN, POS_TYPE_SHORT
    )

class DataParser:
    """Parse CMD API responses into structured data"""
    
    @staticmethod
    def parse_positions(data: str) -> List[Dict]:
        """Parse position data from GET POSITIONS response"""
        positions = []
        lines = data.split('\n')
        in_positions = False
        
        for line in lines:
            line = line.strip()
            # Check for exact start marker
            if line == MARKER_POS_START:
                in_positions = True
                continue
            # Check for end marker
            if line == MARKER_POS_END:
                # Don't break - continue to parse any remaining positions
                continue
            # Parse %POS lines - if we see positions, assume we're in positions section
            if line.startswith("%POS"):
                # If we haven't seen start marker but we see positions, assume we're in positions section
                if not in_positions:
                    in_positions = True
                pos = DataParser._parse_position_line(line)
                if pos:
                    positions.append(pos)
                else:
                    logger.warning(f"Failed to parse position line: {line[:100]}")
        return positions
    
    @staticmethod
    def _parse_position_line(line: str) -> Optional[Dict]:
        """Parse a single %POS line"""
        try:
            # Format: %POS Symbol Type Quantity AvgCost InitQuantity InitPrice Realized CreateTime Unrealized
            # That's %POS (1) + 9 fields = 10 parts minimum
            parts = line.split()
            if len(parts) < 10:
                return None
            
            pos_type_map = {
                POS_TYPE_CASH: "cash",
                POS_TYPE_MARGIN: "margin",
                POS_TYPE_SHORT: "short"
            }
            
            return {
                "symbol": parts[1],
                "type": pos_type_map.get(parts[2], parts[2]),
                "quantity": int(parts[3]),
                "avg_cost": float(parts[4]),
                "init_quantity": int(parts[5]),
                "init_price": float(parts[6]),
                "realized_pnl": float(parts[7]),
                "create_time": parts[8],
                "unrealized_pnl": float(parts[9]) if len(parts) > 9 else 0.0
            }
        except Exception as e:
            logger.warning(f"Error parsing position line: {e}")
            return None
    
    @staticmethod
    def parse_orders(data: str) -> List[Dict]:
        """Parse order data from GET ORDERS response"""
        orders = []
        lines = data.split('\n')
        in_orders = False
        
        for line in lines:
            line = line.strip()
            # Check for exact start marker (exclude #OrderEnd which also starts with "#Order")
            if line == MARKER_ORDER_START:
                in_orders = True
                continue
            # Check for end marker first (before checking for %ORDER lines)
            if line == MARKER_ORDER_END:
                # Don't break - continue to parse any remaining orders before the end
                continue
            # Parse %ORDER lines - if we see orders, assume we're in orders section
            if line.startswith("%ORDER"):
                # If we haven't seen start marker but we see orders, assume we're in orders section
                if not in_orders:
                    in_orders = True
                order = DataParser._parse_order_line(line)
                if order:
                    orders.append(order)
                else:
                    logger.warning(f"Failed to parse order line: {line[:100]}")
        return orders
    
    @staticmethod
    def _parse_order_line(line: str) -> Optional[Dict]:
        """Parse a single %ORDER line"""
        try:
            # Format: %ORDER id token symb b/s mkt/lmt qty lvqty cxlqty price route status time origoid account trader orderSrc
            parts = line.split()
            if len(parts) < 10:
                return None
            
            side_map = {
                "B": "Buy",
                "S": "Sell",
                "SS": "Short"
            }
            
            return {
                "order_id": parts[1],
                "token": parts[2],
                "symbol": parts[3],
                "side": side_map.get(parts[4], parts[4]),
                "order_type": parts[5],
                "quantity": int(parts[6]),
                "left_quantity": int(parts[7]),
                "canceled_quantity": int(parts[8]),
                "price": float(parts[9]) if parts[9] != "MKT" else None,
                "route": parts[10],
                "status": parts[11],
                "time": parts[12] if len(parts) > 12 else "",
                "original_order_id": parts[13] if len(parts) > 13 else "",
                "account": parts[14] if len(parts) > 14 else "",
                "trader": parts[15] if len(parts) > 15 else "",
                "order_source": parts[16] if len(parts) > 16 else ""
            }
        except Exception as e:
            logger.warning(f"Error parsing order line: {e}")
            return None
    
    @staticmethod
    def parse_trades(data: str) -> List[Dict]:
        """Parse trade data from GET TRADES response"""
        trades = []
        lines = data.split('\n')
        in_trades = False
        
        for line in lines:
            line = line.strip()
            # Check for exact start marker (not just startsWith to avoid matching #TradeEnd)
            if line == MARKER_TRADE_START:
                in_trades = True
                continue
            # Check for end marker
            if line == MARKER_TRADE_END:
                # Don't break - continue to parse any remaining trades
                continue
            # Parse %TRADE lines - if we see trades, assume we're in trades section
            if line.startswith("%TRADE"):
                # If we haven't seen start marker but we see trades, assume we're in trades section
                if not in_trades:
                    in_trades = True
                trade = DataParser._parse_trade_line(line)
                if trade:
                    trades.append(trade)
                else:
                    logger.warning(f"Failed to parse trade line: {line[:100]}")
        return trades
    
    @staticmethod
    def _parse_trade_line(line: str) -> Optional[Dict]:
        """Parse a single %TRADE line"""
        try:
            # Format: %TRADE id symb B/S qty price route time orderid Liq EcnFee PL
            parts = line.split()
            if len(parts) < 8:
                return None
            
            return {
                "trade_id": parts[1],
                "symbol": parts[2],
                "side": parts[3],
                "quantity": int(parts[4]),
                "price": float(parts[5]),
                "route": parts[6],
                "time": parts[7],
                "order_id": parts[8] if len(parts) > 8 else "",
                "liquidity": parts[9] if len(parts) > 9 else "",
                "ecn_fee": float(parts[10]) if len(parts) > 10 and parts[10] else 0.0,
                "realized_pl": float(parts[11]) if len(parts) > 11 and parts[11] else 0.0
            }
        except Exception as e:
            print(f"Error parsing trade line: {e}")
            return None
    
    @staticmethod
    def parse_account_info(data: str) -> Optional[Dict]:
        """Parse account info from $AccountInfo response"""
        try:
            logger.debug(f"Parsing account info data: {len(data)} chars, preview: {data[:200]}")
            # Format: $AccountInfo OpenEQ CurrEQ RealizedPL UnrealizedPL NetPL HTBCost SecFee FINRAFee ECNFee Commission
            # Note: Response may start with header line #ACCOUNTINFO before the actual $AccountInfo line
            lines = data.split('\n')
            for line in lines:
                line = line.strip()
                # Look for the actual data line (starts with $AccountInfo)
                if line.startswith("$AccountInfo"):
                    parts = line.split()
                    logger.debug(f"AccountInfo line has {len(parts)} parts: {line[:200]}")
                    if len(parts) >= 11:
                        result = {
                            "open_equity": float(parts[1]),
                            "current_equity": float(parts[2]),
                            "realized_pl": float(parts[3]),
                            "unrealized_pl": float(parts[4]),
                            "net_pl": float(parts[5]),
                            "htb_cost": float(parts[6]),
                            "sec_fee": float(parts[7]),
                            "finra_fee": float(parts[8]),
                            "ecn_fee": float(parts[9]),
                            "commission": float(parts[10])
                        }
                        logger.info(f"Parsed account info: current_equity={result['current_equity']}, net_pl={result['net_pl']}")
                        return result
                    else:
                        logger.warning(f"AccountInfo line has insufficient parts ({len(parts)} < 11): {line[:200]}")
            
            # If we only got the header line, log it
            if any("#ACCOUNTINFO" in line.upper() for line in lines):
                logger.warning(f"Got account info header but no data line. Full response: {data[:500]}")
            else:
                logger.warning(f"$AccountInfo marker not found in data: {data[:200]}")
        except Exception as e:
            logger.error(f"Error parsing account info: {e}", exc_info=True)
        return None
    
    @staticmethod
    def parse_buying_power(data: str) -> Optional[Dict]:
        """Parse buying power from BP response"""
        try:
            logger.debug(f"Parsing buying power data: {len(data)} chars, preview: {data[:200]}")
            # Note: Response may start with header line #buyingpower before the actual BP line
            lines = data.split('\n')
            for line in lines:
                line = line.strip()
                # Look for the actual data line (starts with BP, not #buyingpower)
                if line.startswith("BP") and not line.startswith("#"):
                    parts = line.split()
                    logger.debug(f"BP line has {len(parts)} parts: {line[:200]}")
                    if len(parts) >= 3:
                        result = {
                            "current_bp": float(parts[1]),
                            "overnight_bp": float(parts[2])
                        }
                        logger.info(f"Parsed buying power: current_bp={result['current_bp']}, overnight_bp={result['overnight_bp']}")
                        return result
                    else:
                        logger.warning(f"BP line has insufficient parts ({len(parts)} < 3): {line[:200]}")
            
            # If we only got the header line, log it
            if any("#buyingpower" in line.lower() or "#BUYINGPOWER" in line.upper() for line in lines):
                logger.warning(f"Got buying power header but no data line. Full response: {data[:500]}")
            else:
                logger.warning(f"BP marker not found in data: {data[:200]}")
        except Exception as e:
            logger.error(f"Error parsing buying power: {e}", exc_info=True)
        return None
    
    @staticmethod
    def parse_quote(data: str) -> Optional[Dict]:
        """Parse quote data from $Quote response"""
        try:
            # Format: $Quote symbol A:askprice Asz:asksize B:bidprice Bsz:bidsize V:volume L:lastprice ...
            if not data.startswith("$Quote"):
                return None
            
            quote = {}
            parts = data.split()
            quote["symbol"] = parts[1]
            
            for part in parts[2:]:
                if ':' in part:
                    key, value = part.split(':', 1)
                    try:
                        if key in ['A', 'B', 'L', 'Hi', 'Lo', 'op', 'ycl', 'tcl', 'VWAP']:
                            quote[key.lower()] = float(value)
                        elif key in ['Asz', 'Bsz', 'V']:
                            quote[key.lower()] = int(value)
                        else:
                            quote[key.lower()] = value
                    except:
                        quote[key.lower()] = value
            
            return quote
        except Exception as e:
            print(f"Error parsing quote: {e}")
        return None
    
    @staticmethod
    def parse_order_action(line: str) -> Optional[Dict]:
        """Parse %OrderAct line"""
        try:
            # Format: %OrderAct id ActionType B/S symbol qty price route time notes token
            parts = line.split()
            if len(parts) < 8:
                return None
            
            return {
                "order_id": parts[1],
                "action_type": parts[2],
                "side": parts[3],
                "symbol": parts[4],
                "quantity": int(parts[5]),
                "price": float(parts[6]) if parts[6] != "MKT" else None,
                "route": parts[7],
                "time": parts[8],
                "notes": parts[9] if len(parts) > 9 else "",
                "token": parts[10] if len(parts) > 10 else ""
            }
        except Exception as e:
            print(f"Error parsing order action: {e}")
            return None

