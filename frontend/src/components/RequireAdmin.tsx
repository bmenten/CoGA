import React from 'react';
import { Navigate, Outlet, useLocation } from 'react-router';
import { hasSuperuserRights, isAdmin, isAuthenticated, signInPath } from '../lib/auth';

type RequireAdminProps = {
  /**
   * Let a superuser in too: the workspaces of the configuration the analyses read (gene panels,
   * variant tags, filter presets, sequencing QC thresholds) and package import. Every other admin
   * page is an admin's alone.
   */
  superuser?: boolean;
};

const RequireAdmin: React.FC<RequireAdminProps> = ({ superuser = false }) => {
  const location = useLocation();
  if (!isAuthenticated()) {
    return <Navigate to={signInPath(`${location.pathname}${location.search}${location.hash}`)} replace />;
  }

  const allowed = superuser ? hasSuperuserRights() : isAdmin();
  return allowed ? <Outlet /> : <Navigate to="/dashboard" replace />;
};

export default RequireAdmin;
