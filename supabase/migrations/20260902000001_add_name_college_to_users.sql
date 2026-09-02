-- Supabase Migration: Add participant name and college to users table
-- Project: edsgttjugmindpuqisec
-- Date: 2026-09-02

ALTER TABLE IF EXISTS public.users
ADD COLUMN IF NOT EXISTS name text,
ADD COLUMN IF NOT EXISTS college text;
