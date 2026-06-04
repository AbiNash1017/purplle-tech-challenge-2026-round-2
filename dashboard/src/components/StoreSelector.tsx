"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { ShoppingBag, ArrowLeft, Sun, Moon } from "lucide-react";
import { useTheme } from "@/context/ThemeContext";

interface StoreSelectorProps {
    currentStoreId?: string;
}

export default function StoreSelector({ currentStoreId }: StoreSelectorProps) {
    const pathname = usePathname();
    const { theme, toggleTheme } = useTheme();
    
    const stores = [
        { id: "ST1076", name: "Store 1", location: "Mumbai Central" },
        { id: "ST1008", name: "Store 2", location: "Delhi SelectCitywalk" }
    ];

    return (
        <header className="sticky top-0 z-50 w-full border-b border-zinc-200 dark:border-zinc-800/80 bg-zinc-50/80 dark:bg-zinc-950/80 backdrop-blur-md transition-colors duration-300">
            <div className="mx-auto flex h-16 max-w-7xl items-center justify-between px-4 sm:px-6 lg:px-8">
                {/* Logo & Navigation */}
                <div className="flex items-center gap-6">
                    {pathname !== "/" ? (
                        <Link 
                            href="/" 
                            className="flex items-center gap-2 text-sm font-medium text-zinc-600 dark:text-zinc-400 hover:text-zinc-900 dark:hover:text-zinc-50 transition-colors"
                        >
                            <ArrowLeft className="h-4 w-4" />
                            <span>Dashboard</span>
                        </Link>
                    ) : (
                        <div className="flex items-center gap-2">
                            <div className="h-6 w-1 bg-purple-500 rounded-full animate-pulse" />
                            <span className="text-sm sm:text-md font-semibold tracking-wider text-zinc-900 dark:text-zinc-50 uppercase">
                                Purplle Store Intelligence
                            </span>
                        </div>
                    )}
                </div>

                {/* Store Selection Tabs */}
                <nav className="flex items-center gap-1 bg-zinc-200/50 dark:bg-zinc-900/50 border border-zinc-300 dark:border-zinc-800 p-1 rounded-full">
                    {stores.map((store) => {
                        const isActive = currentStoreId === store.id;
                        return (
                            <Link
                                key={store.id}
                                href={`/store/${store.id}`}
                                className={`flex items-center gap-2 rounded-full px-4 py-1.5 text-xs font-medium transition-all ${
                                    isActive
                                        ? "bg-zinc-950 text-zinc-50 dark:bg-zinc-50 dark:text-zinc-950 font-semibold shadow-sm"
                                        : "text-zinc-600 dark:text-zinc-400 hover:text-zinc-950 dark:hover:text-zinc-50 hover:bg-zinc-300/30 dark:hover:bg-zinc-800/30"
                                }`}
                            >
                                <ShoppingBag className="h-3.5 w-3.5" />
                                <span>{store.name}</span>
                                <span className="opacity-60 hidden sm:inline">({store.location})</span>
                            </Link>
                        );
                    })}
                </nav>

                {/* Right Side: Theme Toggle & Staff Indicator */}
                <div className="flex items-center gap-4">
                    {/* Exclude Staff Badge */}
                    <div className="hidden md:flex items-center gap-2 border border-purple-500/25 bg-purple-500/5 rounded-full px-3 py-1">
                        <span className="h-1.5 w-1.5 rounded-full bg-purple-500 animate-ping" />
                        <span className="text-[10px] uppercase font-bold tracking-wider text-purple-600 dark:text-purple-400">
                            Staff Filter Active
                        </span>
                    </div>

                    {/* Theme Toggle Button */}
                    <button
                        onClick={toggleTheme}
                        className="rounded-full border border-zinc-200 dark:border-zinc-800 bg-zinc-100 dark:bg-zinc-900 p-2 text-zinc-700 dark:text-zinc-300 hover:bg-zinc-200 dark:hover:bg-zinc-800 hover:text-zinc-950 dark:hover:text-zinc-50 transition-all"
                        aria-label="Toggle Theme"
                    >
                        {theme === "light" ? (
                            <Moon className="h-4 w-4" />
                        ) : (
                            <Sun className="h-4 w-4" />
                        )}
                    </button>
                </div>
            </div>
        </header>
    );
}
