-- SICM IPL Mock Auction 2026 - Supabase PostgreSQL Schema
-- Project: edsgttjugmindpuqisec

-- ENUMS
DO $$ BEGIN
    CREATE TYPE public.team_code AS ENUM ('RCB', 'CSK', 'MI', 'KKR', 'SRH', 'RR', 'DC', 'PBKS', 'GT', 'LSG');
EXCEPTION
    WHEN duplicate_object THEN null;
END $$;

DO $$ BEGIN
    CREATE TYPE public.user_role AS ENUM ('participant', 'admin');
EXCEPTION
    WHEN duplicate_object THEN null;
END $$;

DO $$ BEGIN
    CREATE TYPE public.player_role AS ENUM ('Batsman', 'Bowler', 'All-Rounder', 'Wicket Keeper');
EXCEPTION
    WHEN duplicate_object THEN null;
END $$;

-- TEAMS TABLE
CREATE TABLE IF NOT EXISTS public.teams (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    code public.team_code NOT NULL UNIQUE,
    name TEXT NOT NULL,
    wallet_balance BIGINT NOT NULL DEFAULT 1000000000 CHECK (wallet_balance >= 0),
    max_squad_size INTEGER NOT NULL DEFAULT 12,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- USERS TABLE
CREATE TABLE IF NOT EXISTS public.users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    team_id UUID REFERENCES public.teams(id) ON DELETE SET NULL,
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    role public.user_role NOT NULL DEFAULT 'participant',
    name TEXT,
    college TEXT,
    active BOOLEAN NOT NULL DEFAULT true,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- PLAYERS TABLE
CREATE TABLE IF NOT EXISTS public.players (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL,
    role public.player_role NOT NULL,
    nationality TEXT,
    base_price BIGINT NOT NULL DEFAULT 20000000 CHECK (base_price >= 0),
    photo_url TEXT,
    notes TEXT,
    auction_order INTEGER NOT NULL DEFAULT 0,
    is_available BOOLEAN NOT NULL DEFAULT true,
    sold_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- PURCHASES TABLE
CREATE TABLE IF NOT EXISTS public.purchases (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    player_id UUID NOT NULL REFERENCES public.players(id) ON DELETE CASCADE,
    team_id UUID NOT NULL REFERENCES public.teams(id) ON DELETE CASCADE,
    sold_price BIGINT NOT NULL CHECK (sold_price > 0),
    sold_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    recorded_by UUID NOT NULL REFERENCES public.users(id),
    request_id UUID UNIQUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- AUCTION STATE TABLE
CREATE TABLE IF NOT EXISTS public.auction_state (
    id BOOLEAN PRIMARY KEY DEFAULT true,
    current_player_id UUID REFERENCES public.players(id) ON DELETE SET NULL,
    is_live BOOLEAN NOT NULL DEFAULT false,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT single_row CHECK (id = true)
);

-- RPC CONTRACTS

-- 1. register_participant
-- Signature: register_participant(p_team team_code, p_username text, p_password_hash text) RETURNS jsonb
-- Atomic transaction that validates and registers a team participant with hashed password.

-- 2. mark_player_sold
-- Signature: mark_player_sold(p_player_id uuid, p_team team_code, p_sold_price bigint, p_recorded_by uuid, p_request_id uuid) RETURNS jsonb
-- Atomic transaction with row locking on player and team, enforcing:
--  - PLAYER_NOT_FOUND if invalid player
--  - ALREADY_SOLD if player is not available
--  - LOW_BALANCE if wallet_balance < p_sold_price
--  - SQUAD_FULL if current squad >= max_squad_size (12)
--  - Deducts team wallet_balance
--  - Records purchase with idempotency via request_id
--  - Marks player is_available = false
