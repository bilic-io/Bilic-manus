-- Create user_sandboxes table to track user sandbox relationships
CREATE TABLE public.user_sandboxes (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    sandbox_id TEXT NOT NULL,
    sandbox_pass TEXT NOT NULL,
    last_active_at TIMESTAMP WITH TIME ZONE DEFAULT TIMEZONE('utc'::text, NOW()) NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT TIMEZONE('utc'::text, NOW()) NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT TIMEZONE('utc'::text, NOW()) NOT NULL,
    UNIQUE(user_id)
);

-- Add index for faster lookups
CREATE INDEX idx_user_sandboxes_user_id ON public.user_sandboxes(user_id);
