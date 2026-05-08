# Use Alpine for smaller image
FROM node:20-alpine

WORKDIR /app

# Install dependencies
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

# Copy source
COPY frontend/ ./

# Build and serve
RUN npm run build
EXPOSE 3000
CMD ["npm", "start"]
