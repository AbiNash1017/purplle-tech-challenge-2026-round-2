import { useEffect, useState, useRef } from "react";

export interface WebSocketMessage {
    event_type: string;
    store_id: string;
    track_id: string;
    camera_id: string;
    timestamp: string;
    is_staff?: boolean;
    location?: {
        type: string;
        coordinates: [number, number];
    };
    zone_id?: string;
    zone_name?: string;
    zone_type?: string;
    [key: string]: any;
}

export function useWebSocket(storeId: string, url: string = "ws://localhost:8000/api/v1/ws") {
    const [lastMessage, setLastMessage] = useState<WebSocketMessage | null>(null);
    const [isConnected, setIsConnected] = useState(false);
    const [isIdle, setIsIdle] = useState(false);
    const wsRef = useRef<WebSocket | null>(null);
    const reconnectTimeoutRef = useRef<NodeJS.Timeout | null>(null);
    const reconnectAttemptsRef = useRef(0);

    useEffect(() => {
        if (!storeId) return;

        const connect = () => {
            if (wsRef.current) {
                wsRef.current.close();
            }

            const wsUrl = `${url}/${storeId}`;
            console.log(`Connecting WebSocket to: ${wsUrl}`);
            const socket = new WebSocket(wsUrl);
            wsRef.current = socket;

            socket.onopen = () => {
                console.log(`WebSocket connected for store: ${storeId}`);
                setIsConnected(true);
                setIsIdle(false);
                reconnectAttemptsRef.current = 0;
            };

            socket.onmessage = (event) => {
                try {
                    const data = JSON.parse(event.data) as WebSocketMessage;
                    
                    if (data.event_type === "store_idle") {
                        setIsIdle(true);
                    } else {
                        setIsIdle(false);
                        setLastMessage(data);
                    }
                } catch (err) {
                    console.error("Failed parsing websocket frame", err);
                }
            };

            socket.onclose = () => {
                console.log(`WebSocket closed for store: ${storeId}`);
                setIsConnected(false);
                
                // Exponential backoff reconnect
                const delay = Math.min(1000 * Math.pow(2, reconnectAttemptsRef.current), 30000);
                reconnectAttemptsRef.current += 1;
                
                console.log(`Reconnecting WebSocket in ${delay}ms...`);
                reconnectTimeoutRef.current = setTimeout(connect, delay);
            };

            socket.onerror = (err) => {
                console.warn("WebSocket error:", err);
                socket.close();
            };
        };

        connect();

        return () => {
            if (reconnectTimeoutRef.current) {
                clearTimeout(reconnectTimeoutRef.current);
            }
            if (wsRef.current) {
                // Remove onclose and onerror handlers to prevent reconnect loops and noisy errors on unmount
                wsRef.current.onclose = null;
                wsRef.current.onerror = null;
                wsRef.current.close();
            }
        };
    }, [storeId, url]);

    return { lastMessage, isConnected, isIdle };
}
