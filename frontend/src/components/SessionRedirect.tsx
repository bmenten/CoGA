import React from 'react';
import { Navigate } from 'react-router-dom';
import { isAuthenticated } from '../lib/auth';

interface SessionRedirectProps {
  authenticatedTo: string;
  unauthenticatedTo: string;
}

const SessionRedirect: React.FC<SessionRedirectProps> = ({
  authenticatedTo,
  unauthenticatedTo,
}) => (
  <Navigate to={isAuthenticated() ? authenticatedTo : unauthenticatedTo} replace />
);

export default SessionRedirect;
