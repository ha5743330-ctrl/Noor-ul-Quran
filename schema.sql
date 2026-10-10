create table if not exists public.noor_admins (
    user_id uuid primary key references auth.users(id) on delete cascade,
    created_at timestamptz not null default now()
);

create table if not exists public.noor_user_access (
    user_id uuid primary key references auth.users(id) on delete cascade,
    premium_access boolean not null default false,
    granted_by uuid references auth.users(id) on delete set null,
    updated_at timestamptz not null default now()
);

alter table public.noor_admins enable row level security;
alter table public.noor_user_access enable row level security;

revoke all on public.noor_admins from anon, authenticated;
revoke insert, update, delete on public.noor_user_access from anon, authenticated;
grant select on public.noor_user_access to authenticated;

create or replace function public.noor_is_admin()
returns boolean
language sql
stable
security definer
set search_path = public, auth, pg_temp
as $$
    select exists (
        select 1
        from public.noor_admins
        where user_id = auth.uid()
    );
$$;

revoke all on function public.noor_is_admin() from public, anon;
grant execute on function public.noor_is_admin() to authenticated;

drop policy if exists noor_user_access_read on public.noor_user_access;
create policy noor_user_access_read
    on public.noor_user_access
    for select
    to authenticated
    using (user_id = auth.uid() or public.noor_is_admin());

create or replace function public.noor_my_access()
returns jsonb
language sql
stable
security definer
set search_path = public, auth, pg_temp
as $$
    select jsonb_build_object(
        'user_id', auth.uid(),
        'is_admin', public.noor_is_admin(),
        'premium_access', public.noor_is_admin() or coalesce((
            select access.premium_access
            from public.noor_user_access as access
            where access.user_id = auth.uid()
        ), false)
    );
$$;

create or replace function public.noor_admin_list_users()
returns table (
    user_id uuid,
    email text,
    display_name text,
    premium_access boolean,
    created_at timestamptz
)
language plpgsql
stable
security definer
set search_path = public, auth, pg_temp
as $$
begin
    if not public.noor_is_admin() then
        raise exception 'Admin access required' using errcode = '42501';
    end if;

    return query
    select
        users.id,
        users.email::text,
        coalesce(users.raw_user_meta_data ->> 'full_name', '')::text,
        coalesce(access.premium_access, false) or public.noor_is_admin(),
        users.created_at
    from auth.users as users
    left join public.noor_user_access as access on access.user_id = users.id
    order by users.created_at desc;
end;
$$;

create or replace function public.noor_admin_set_premium(
    target_user_id uuid,
    enabled boolean
)
returns void
language plpgsql
security definer
set search_path = public, auth, pg_temp
as $$
begin
    if not public.noor_is_admin() then
        raise exception 'Admin access required' using errcode = '42501';
    end if;

    insert into public.noor_user_access (user_id, premium_access, granted_by, updated_at)
    values (target_user_id, enabled, auth.uid(), now())
    on conflict (user_id) do update
    set premium_access = excluded.premium_access,
        granted_by = excluded.granted_by,
        updated_at = excluded.updated_at;
end;
$$;

revoke all on function public.noor_my_access() from public, anon;
revoke all on function public.noor_admin_list_users() from public, anon;
revoke all on function public.noor_admin_set_premium(uuid, boolean) from public, anon;
grant execute on function public.noor_my_access() to authenticated;
grant execute on function public.noor_admin_list_users() to authenticated;
grant execute on function public.noor_admin_set_premium(uuid, boolean) to authenticated;

insert into public.noor_admins (user_id)
values ('<YOUR_ADMIN_USER_ID>')
on conflict (user_id) do nothing;

insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
values (
    'noor-media',
    'noor-media',
    false,
    52428800,
    array[
        'video/mp4',
        'video/quicktime',
        'audio/mpeg',
        'audio/mp4',
        'audio/aac',
        'audio/wav',
        'text/csv',
        'application/json'
    ]
)
on conflict (id) do update
set public = excluded.public,
    file_size_limit = excluded.file_size_limit,
    allowed_mime_types = excluded.allowed_mime_types;